"""Unit tests for resolver.py against a fake Telethon client - no network,
no session.

The behaviour that matters most here is what the worker does about rate
limits: it must WAIT the amount Telegram asks for, and must not resolve
anything it already has cached. Both are asserted directly (recorded sleeps,
recorded get_entity calls) rather than inferred.
"""

from datetime import datetime, timedelta, timezone

import pytest
from telethon.errors import ChannelPrivateError, FloodWaitError
from telethon.tl.types import Channel, InputPeerChannel

from workers.telegram_monitor.channel_cache import (
    STATUS_NOT_FOUND,
    STATUS_NOT_JOINED,
    STATUS_OK,
    ChannelCache,
    make_entry,
)
from workers.telegram_monitor.config import ChannelEntry, Settings
from workers.telegram_monitor.resolver import ChannelResolver, ResolvedChannel


def iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _channel(channel_id: int, access_hash: int = 777, title: str = "Канал") -> Channel:
    """A REAL Telethon Channel, not a stand-in: the resolver runs entities
    through telethon.utils.get_peer_id, which rejects anything that isn't an
    actual TLObject - so a hand-rolled fake would pass tests the real thing
    would fail."""
    return Channel(id=channel_id, title=title, photo=None, date=None, access_hash=access_hash)


def _flood(seconds: int) -> FloodWaitError:
    error = FloodWaitError(request=None)
    error.seconds = seconds
    return error


class _FakeDialog:
    def __init__(self, entity):
        self.entity = entity


class _FakeClient:
    """Records every get_entity call so tests can assert on what was - and
    crucially, what was NOT - asked of Telegram."""

    def __init__(self, entities=None, errors=None, dialogs=None, dialogs_error=None):
        self._entities = entities or {}
        self._errors = errors or {}
        self._dialogs = dialogs
        self._dialogs_error = dialogs_error
        self.resolve_calls: list[str] = []
        self.dialog_calls = 0

    async def get_entity(self, target):
        key = str(target).lower()
        self.resolve_calls.append(key)
        if key in self._errors:
            error = self._errors[key]
            # A list means "raise these in order across successive calls".
            if isinstance(error, list):
                raise error.pop(0) if len(error) > 1 else error[0]
            raise error
        if key in self._entities:
            return self._entities[key]
        raise ValueError(f"No user has {target!r} as username")

    async def get_dialogs(self, limit=None):
        self.dialog_calls += 1
        if self._dialogs_error is not None:
            raise self._dialogs_error
        return [_FakeDialog(e) for e in (self._dialogs or [])]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        tg_api_id=1,
        tg_api_hash="test-hash",
        channel_cache_file=tmp_path / "channel_cache.json",
        resolve_delay_seconds=0,
        cache_ttl_days=30,
        max_flood_wait_seconds=300,
        force_resolve=False,
    )


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """Captures every asyncio.sleep the resolver performs, so a test can
    assert the worker actually waited (and for how long) rather than
    skipping the pause."""
    recorded: list[float] = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr("workers.telegram_monitor.resolver.asyncio.sleep", fake_sleep)
    return recorded


def cache_at(tmp_path) -> ChannelCache:
    return ChannelCache(tmp_path / "channel_cache.json")


# --- the core win: a warm cache means zero resolves ------------------------


@pytest.mark.asyncio
async def test_cached_channels_are_not_resolved_again(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    cache.put("fordev", make_entry(status=STATUS_OK, id=-1001, access_hash=5, resolved_at=iso_days_ago(1)))
    cache.put("jsdevjob", make_entry(status=STATUS_OK, id=-1002, access_hash=6, resolved_at=iso_days_ago(2)))
    client = _FakeClient()

    outcome = await ChannelResolver(client, settings, cache).resolve(
        [ChannelEntry(username="fordev"), ChannelEntry(username="jsdevjob")])

    # This is the whole point of the change.
    assert client.resolve_calls == []
    assert client.dialog_calls == 0
    assert outcome.from_cache == 2
    assert outcome.resolved_now == 0
    assert outcome.peer_ids == [-1001, -1002]


@pytest.mark.asyncio
async def test_only_the_uncached_channel_is_resolved(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    cache.put("cached", make_entry(status=STATUS_OK, id=-1001, resolved_at=iso_days_ago(1)))
    entity = _channel(2002)
    client = _FakeClient(entities={"fresh": entity}, dialogs=[entity])

    outcome = await ChannelResolver(client, settings, cache).resolve(
        [ChannelEntry(username="cached"), ChannelEntry(username="fresh")])

    assert client.resolve_calls == ["fresh"]
    assert outcome.from_cache == 1
    assert outcome.resolved_now == 1
    assert len(outcome.channels) == 2


@pytest.mark.asyncio
async def test_a_stale_entry_is_re_resolved(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    cache.put("old", make_entry(status=STATUS_OK, id=-1001, resolved_at=iso_days_ago(90)))
    entity = _channel(3003)
    client = _FakeClient(entities={"old": entity}, dialogs=[entity])

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="old")])

    assert client.resolve_calls == ["old"]
    assert outcome.resolved_now == 1


@pytest.mark.asyncio
async def test_force_resolve_ignores_a_perfectly_good_cache(settings, tmp_path, slept):
    settings.force_resolve = True
    cache = cache_at(tmp_path)
    cache.put("fordev", make_entry(status=STATUS_OK, id=-1001, resolved_at=iso_days_ago(1)))
    entity = _channel(4004)
    client = _FakeClient(entities={"fordev": entity}, dialogs=[entity])

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="fordev")])

    assert client.resolve_calls == ["fordev"]
    assert outcome.from_cache == 0
    assert outcome.resolved_now == 1


@pytest.mark.asyncio
async def test_results_are_written_to_the_cache_file_for_the_next_run(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    entity = _channel(5005, access_hash=888, title="Заголовок")
    client = _FakeClient(entities={"fordev": entity}, dialogs=[entity])

    await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="fordev")])

    from workers.telegram_monitor.channel_cache import load_cache

    reloaded = load_cache(tmp_path / "channel_cache.json")
    entry = reloaded.get("fordev")
    assert entry["status"] == STATUS_OK
    assert entry["access_hash"] == 888
    assert entry["title"] == "Заголовок"


# --- negative results are cached too --------------------------------------


@pytest.mark.asyncio
async def test_a_dead_channel_is_cached_as_not_found(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    client = _FakeClient(entities={}, dialogs=[])

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="gone")])

    assert outcome.not_found == 1
    assert outcome.channels == []
    assert cache.get("gone")["status"] == STATUS_NOT_FOUND


@pytest.mark.asyncio
async def test_a_dead_channel_is_not_resolved_again_on_the_next_run(settings, tmp_path, slept):
    """Without caching negatives, every dead entry in channels.yml would burn
    a ResolveUsername call on every single start."""
    cache = cache_at(tmp_path)
    client = _FakeClient(entities={}, dialogs=[])
    entries = [ChannelEntry(username="gone")]

    await ChannelResolver(client, settings, cache).resolve(entries)
    calls_after_first_run = len(client.resolve_calls)
    await ChannelResolver(client, settings, cache).resolve(entries)

    assert calls_after_first_run == 1
    assert len(client.resolve_calls) == 1  # unchanged by the second run


@pytest.mark.asyncio
async def test_private_channel_is_cached_as_not_joined(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    client = _FakeClient(errors={"secret": ChannelPrivateError(request=None)}, dialogs=[])

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="secret")])

    assert outcome.not_joined == 1
    assert cache.get("secret")["status"] == STATUS_NOT_JOINED


# --- not-joined detection & skipping --------------------------------------


@pytest.mark.asyncio
async def test_public_channel_we_are_not_a_member_of_is_skipped(settings, tmp_path, slept):
    """A public channel resolves fine whether or not we've joined it, so
    resolution success alone doesn't mean we'd receive its messages -
    membership comes from the dialog list."""
    cache = cache_at(tmp_path)
    joined = _channel(1111)
    not_joined = _channel(2222)
    client = _FakeClient(
        entities={"joined": joined, "lurking": not_joined},
        dialogs=[joined],  # only one of them is in our dialogs
    )

    outcome = await ChannelResolver(client, settings, cache).resolve(
        [ChannelEntry(username="joined"), ChannelEntry(username="lurking")])

    assert [c.key for c in outcome.channels] == ["joined"]
    assert outcome.not_joined == 1
    assert cache.get("lurking")["status"] == STATUS_NOT_JOINED
    # One dialog request covered both channels, not one call each.
    assert client.dialog_calls == 1


@pytest.mark.asyncio
async def test_not_joined_channels_are_skipped_from_cache_without_re_resolving(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    cache.put("lurking", make_entry(status=STATUS_NOT_JOINED, resolved_at=iso_days_ago(1)))
    client = _FakeClient()

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="lurking")])

    assert client.resolve_calls == []
    assert outcome.not_joined == 1
    assert outcome.channels == []


@pytest.mark.asyncio
async def test_membership_is_left_alone_when_the_dialog_list_is_unavailable(settings, tmp_path, slept):
    """If we can't fetch dialogs we don't know who we've joined - so nothing
    gets written off as not_joined on a guess."""
    cache = cache_at(tmp_path)
    entity = _channel(3333)
    client = _FakeClient(
        entities={"fordev": entity},
        dialogs_error=RuntimeError("dialogs unavailable"),
    )

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="fordev")])

    assert outcome.not_joined == 0
    assert [c.key for c in outcome.channels] == ["fordev"]


# --- FloodWait: we WAIT, we do not work around it -------------------------


@pytest.mark.asyncio
async def test_flood_wait_is_waited_out_then_the_resolve_succeeds(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    entity = _channel(4444)
    client = _FakeClient(entities={"fordev": entity}, dialogs=[entity])

    # First call floods for 51s (the real escalation the user hit), the
    # retry after the wait succeeds.
    original = client.get_entity
    calls = {"n": 0}

    async def floods_once(target):
        calls["n"] += 1
        if calls["n"] == 1:
            client.resolve_calls.append(str(target).lower())
            raise _flood(51)
        return await original(target)

    client.get_entity = floods_once

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="fordev")])

    # Slept for exactly what Telegram asked for - not skipped, not shortened,
    # not routed around.
    assert 51 in slept
    assert outcome.resolved_now == 1
    assert len(outcome.channels) == 1


@pytest.mark.asyncio
async def test_flood_wait_over_the_ceiling_stops_resolving_and_keeps_progress(settings, tmp_path, slept):
    settings.max_flood_wait_seconds = 300
    cache = cache_at(tmp_path)
    first = _channel(5555)
    client = _FakeClient(entities={"first": first}, dialogs=[first])

    original = client.get_entity

    async def floods_hard_on_second(target):
        key = str(target).lower()
        if key == "second":
            client.resolve_calls.append(key)
            raise _flood(3600)
        return await original(target)

    client.get_entity = floods_hard_on_second

    outcome = await ChannelResolver(client, settings, cache).resolve(
        [ChannelEntry(username="first"), ChannelEntry(username="second"), ChannelEntry(username="third")])

    # Stopped rather than blocking the worker for an hour...
    assert outcome.aborted_reason is not None
    assert "3600" in outcome.aborted_reason
    # ...and crucially never slept the over-ceiling amount either.
    assert 3600 not in slept
    # ...but kept, and monitors, what it had already resolved.
    assert [c.key for c in outcome.channels] == ["first"]
    assert outcome.unresolved == 2
    # "third" was never even attempted - we stop poking a throttled account.
    assert "third" not in client.resolve_calls


@pytest.mark.asyncio
async def test_progress_before_an_abort_is_saved_to_disk(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    first = _channel(6666)
    client = _FakeClient(entities={"first": first}, dialogs=[first])

    original = client.get_entity

    async def floods_hard_on_second(target):
        if str(target).lower() == "second":
            raise _flood(9999)
        return await original(target)

    client.get_entity = floods_hard_on_second

    await ChannelResolver(client, settings, cache).resolve(
        [ChannelEntry(username="first"), ChannelEntry(username="second")])

    from workers.telegram_monitor.channel_cache import load_cache

    reloaded = load_cache(tmp_path / "channel_cache.json")
    # The resolve we already paid for survives, so the next run doesn't
    # repeat it and walk back into the same rate limit.
    assert reloaded.get("first")["status"] == STATUS_OK
    assert reloaded.get("second") is None  # never resolved, so nothing claimed about it


@pytest.mark.asyncio
async def test_a_second_flood_wait_in_a_row_stops_the_pass(settings, tmp_path, slept):
    """An escalating limit means the account is genuinely throttled; retrying
    harder is exactly the wrong response."""
    cache = cache_at(tmp_path)
    client = _FakeClient(dialogs=[])
    calls = {"n": 0}

    async def always_floods(target):
        calls["n"] += 1
        raise _flood(10) if calls["n"] == 1 else _flood(120)

    client.get_entity = always_floods

    outcome = await ChannelResolver(client, settings, cache).resolve([ChannelEntry(username="fordev")])

    assert calls["n"] == 2  # waited once, retried once, then gave up
    assert 10 in slept
    assert outcome.aborted_reason is not None
    assert outcome.channels == []


@pytest.mark.asyncio
async def test_pauses_between_consecutive_resolves(settings, tmp_path, slept):
    settings.resolve_delay_seconds = 2.0
    cache = cache_at(tmp_path)
    a, b, c = _channel(1), _channel(2), _channel(3)
    client = _FakeClient(entities={"a": a, "b": b, "c": c}, dialogs=[a, b, c])

    await ChannelResolver(client, settings, cache).resolve(
        [ChannelEntry(username="a"), ChannelEntry(username="b"), ChannelEntry(username="c")])

    # Three resolves => two gaps between them (no pointless pause up front).
    assert slept.count(2.0) == 2


# --- summary line ---------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_line_reports_monitored_and_skipped_counts(settings, tmp_path, slept):
    cache = cache_at(tmp_path)
    joined = _channel(1)
    lurking = _channel(2)
    client = _FakeClient(entities={"joined": joined, "lurking": lurking}, dialogs=[joined])

    outcome = await ChannelResolver(client, settings, cache).resolve([
        ChannelEntry(username="joined"),
        ChannelEntry(username="lurking"),
        ChannelEntry(username="gone"),
    ])

    summary = outcome.summary_line()

    assert "Мониторю 1 каналов" in summary
    assert "1 не подписан" in summary
    assert "1 не найдено" in summary


# --- cached peers are usable without a resolve ----------------------------


def test_resolved_channel_builds_an_input_peer_from_cached_values():
    channel = ResolvedChannel(key="fordev", peer_id=-1001234567890, access_hash=42)

    peer = channel.to_input_peer()

    # An InputPeer* is returned by Telethon's get_input_entity unchanged, so
    # using one costs no ResolveUsername call.
    assert isinstance(peer, InputPeerChannel)
    assert peer.channel_id == 1234567890
    assert peer.access_hash == 42


def test_resolved_channel_without_an_access_hash_falls_back_to_the_marked_id():
    channel = ResolvedChannel(key="fordev", peer_id=-1001234567890, access_hash=None)

    assert channel.to_input_peer() == -1001234567890
