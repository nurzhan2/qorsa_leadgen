"""Unit tests for channel_rater.py.

ChannelRating's order_rate/verdict are pure and tested directly. rate_channel
needs a Telethon-like client, so tests use a small in-memory fake exposing
just the one call channel_rater.py makes on it (iter_messages) - no real
network, no real Telegram session.

Note the rater no longer resolves anything itself: it takes already-resolved
channels from resolver.py, which reads them from the cache. That's why these
tests hand it ResolvedChannel objects rather than channels.yml entries.
"""

import pytest

from workers.telegram_monitor.channel_cache import (
    STATUS_NOT_FOUND,
    STATUS_NOT_JOINED,
    STATUS_OK,
    ChannelCache,
    make_entry,
)
from workers.telegram_monitor.channel_rater import ChannelRater, ChannelRating, skipped_ratings
from workers.telegram_monitor.config import ChannelEntry, KeywordCategory, KeywordsConfig, Settings
from workers.telegram_monitor.matcher import Matcher
from workers.telegram_monitor.resolver import ResolvedChannel

# --- ChannelRating: order_rate / verdict, pure ---------------------------


def test_order_rate_is_zero_with_no_posts():
    rating = ChannelRating(identifier="x")

    assert rating.order_rate == 0.0


def test_order_rate_calculation():
    rating = ChannelRating(identifier="x", subscribed=True, total_posts=20, orders=5)

    assert rating.order_rate == 0.25


@pytest.mark.parametrize(
    ("orders", "total", "expected_verdict"),
    [
        (3, 20, "ДЕРЖАТЬ"),  # 15% - exactly at the "keep" threshold
        (10, 20, "ДЕРЖАТЬ"),  # 50% - well above
        (2, 20, "СЛАБО"),  # 10% - in the weak band
        (1, 20, "СЛАБО"),  # 5% - exactly at the "weak" threshold
        (0, 20, "ВЫКИНУТЬ"),  # 0% - below weak threshold
    ],
)
def test_verdict_thresholds(orders, total, expected_verdict):
    rating = ChannelRating(identifier="x", subscribed=True, total_posts=total, orders=orders)

    assert rating.verdict == expected_verdict


def test_verdict_unreachable_when_not_subscribed():
    rating = ChannelRating(identifier="x", subscribed=False, total_posts=0, orders=0)

    assert rating.verdict == "НЕДОСТУПЕН"


def test_verdict_unreachable_when_subscribed_but_zero_posts_read():
    # e.g. a channel that resolved fine but has no history at all - should
    # not be reported as "ВЫКИНУТЬ" (that implies we actually evaluated
    # content and found it weak).
    rating = ChannelRating(identifier="x", subscribed=True, total_posts=0, orders=0)

    assert rating.verdict == "НЕДОСТУПЕН"


# --- ChannelRater.rate_channel: counts orders/hiring/noise from mocked ---
# --- post history, via a fake Telethon-like client -----------------------


class _FakeMessage:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text


class _FakeClient:
    """Exposes only what channel_rater.py actually calls on a Telethon
    client now: iter_messages() (an async iterator, NOT awaited itself -
    matches real Telethon usage). Notably there is NO get_entity here: the
    rater must never resolve, and this fake would blow up if it tried."""

    def __init__(self, posts_by_peer: dict, unreadable: set | None = None):
        self._posts_by_peer = posts_by_peer
        self._unreadable = unreadable or set()

    def iter_messages(self, entity, limit):
        # The rater passes a cached peer (an int here, since these fixtures
        # have no access_hash), never a username.
        if entity in self._unreadable:
            raise ValueError(f"cannot read history: {entity}")
        posts = self._posts_by_peer.get(entity, [])

        async def _gen():
            for text in posts[:limit]:
                yield _FakeMessage(text)

        return _gen()


def _resolved(peer_id: int, key: str) -> ResolvedChannel:
    """A channel as the resolver hands it over: no access_hash, so
    to_input_peer() returns the plain marked id our fake keys on."""
    return ResolvedChannel(key=key, peer_id=peer_id, access_hash=None)


@pytest.fixture
def keywords() -> KeywordsConfig:
    return KeywordsConfig(
        intent=KeywordCategory(weight=3, keywords=["нужен сайт", "ищу разработчика"]),
        domain=KeywordCategory(weight=2, keywords=["сайт", "лендинг", "бот"]),
        budget=KeywordCategory(weight=1, keywords=["бюджет"]),
        anti_hiring=["в штат", "оклад", "график работы"],
        order_signals=["разовая задача", "бюджет"],
    )


@pytest.fixture
def matcher(keywords: KeywordsConfig) -> Matcher:
    return Matcher(keywords)


@pytest.fixture
def settings() -> Settings:
    return Settings(tg_api_id=1, tg_api_hash="test-hash", rate_sample=50, rate_channel_delay_seconds=0)


@pytest.mark.asyncio
async def test_rate_channel_counts_orders_hiring_and_noise(settings: Settings, matcher: Matcher):
    posts = [
        "Нужен сайт под ключ, бюджет 50000",  # order (intent)
        "Есть бюджет, разовая задача на лендинг",  # order (domain + order_signals)
        "Ищу разработчика в штат, оклад 150000, график работы 5/2",  # hiring (rejected)
        "Всем привет, как дела?",  # noise
        "Красивый закат сегодня, всем добра",  # noise
    ]
    client = _FakeClient(posts_by_peer={-1001: posts})
    rater = ChannelRater(settings, matcher, client)

    rating = await rater.rate_channel(_resolved(-1001, "good_channel"))

    assert rating.subscribed is True
    assert rating.total_posts == 5
    assert rating.orders == 2
    assert rating.hiring == 1
    assert rating.noise == 2
    assert rating.order_rate == pytest.approx(2 / 5)
    assert rating.verdict == "ДЕРЖАТЬ"  # 40% order rate


@pytest.mark.asyncio
async def test_rate_channel_reads_history_without_ever_resolving(settings: Settings, matcher: Matcher):
    """The regression this whole change exists to prevent: the rater used to
    call get_entity() per channel, which is what triggered the FloodWait.
    _FakeClient has no get_entity at all, so any attempt would raise."""
    client = _FakeClient(posts_by_peer={-1001: ["Нужен сайт под ключ, бюджет 50000"]})
    rater = ChannelRater(settings, matcher, client)

    rating = await rater.rate_channel(_resolved(-1001, "good_channel"))

    assert not hasattr(client, "get_entity")
    assert rating.total_posts == 1


@pytest.mark.asyncio
async def test_rate_channel_survives_an_unreadable_history(settings: Settings, matcher: Matcher):
    client = _FakeClient(posts_by_peer={}, unreadable={-1002})
    rater = ChannelRater(settings, matcher, client)

    rating = await rater.rate_channel(_resolved(-1002, "broken_channel"))

    assert rating.subscribed is False
    assert rating.total_posts == 0
    assert rating.skip_reason == "read_failed"
    assert rating.verdict == "НЕДОСТУПЕН"


@pytest.mark.asyncio
async def test_rate_all_rates_every_resolved_channel(settings: Settings, matcher: Matcher):
    client = _FakeClient(
        posts_by_peer={
            -1001: ["Нужен сайт под ключ, бюджет 50000"] * 3,
            -1002: [],
        },
    )
    rater = ChannelRater(settings, matcher, client)

    ratings = await rater.rate_all([
        _resolved(-1001, "orders_channel"),
        _resolved(-1002, "empty_channel"),
    ])

    assert [r.identifier for r in ratings] == ["orders_channel", "empty_channel"]
    assert ratings[0].verdict == "ДЕРЖАТЬ"
    assert ratings[1].verdict == "НЕДОСТУПЕН"  # readable but has no posts


# --- skipped channels still show up in the report ------------------------


def test_skipped_channels_are_reported_from_the_cache(tmp_path):
    """Channels the resolver excluded must still appear in the CSV, so the
    user can see WHY a line in channels.yml isn't producing anything."""
    cache = ChannelCache(tmp_path / "c.json")
    cache.put("lurking", make_entry(status=STATUS_NOT_JOINED))
    cache.put("gone", make_entry(status=STATUS_NOT_FOUND))
    cache.put("live", make_entry(status=STATUS_OK, id=-1001))

    rows = skipped_ratings(
        [
            ChannelEntry(username="lurking"),
            ChannelEntry(username="gone"),
            ChannelEntry(username="live"),
        ],
        cache,
    )

    # Only the excluded ones - "live" is rated for real, not listed as skipped.
    assert {r.identifier for r in rows} == {"lurking", "gone"}
    assert all(r.subscribed is False for r in rows)
    assert {r.skip_reason for r in rows} == {STATUS_NOT_JOINED, STATUS_NOT_FOUND}


def test_channels_absent_from_the_cache_are_not_reported_as_skipped(tmp_path):
    cache = ChannelCache(tmp_path / "c.json")

    rows = skipped_ratings([ChannelEntry(username="never_seen")], cache)

    assert rows == []
