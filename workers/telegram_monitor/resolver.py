"""Turns channels.yml entries into peers the worker can actually listen to,
asking Telegram to resolve a username only when the cache can't answer.

This is the one place that talks to Telegram about *resolution*, shared by
monitor.py and channel_rater.py so the rate-limit policy can't drift between
them.

The policy, in full:

  1. Anything already in channel_cache.json and not past its TTL is used
     as-is - no request at all. With a warm cache a restart makes ZERO
     `ResolveUsernameRequest` calls, which is the entire point.
  2. Whatever's left is resolved one at a time, pausing
     RESOLVE_DELAY_SECONDS between calls.
  3. A `FloodWait` is **obeyed, never dodged**: the worker logs how long
     Telegram asked it to wait and waits exactly that long. If the wait is
     longer than MAX_FLOOD_WAIT_SECONDS, resolution stops for this run,
     whatever was resolved so far is saved, and the worker runs with those
     channels. There is no proxy rotation, no second session, no retry
     storm - Telegram asked us to slow down and we do.
  4. Every outcome is cached, including the negative ones, so dead and
     unsubscribed channels cost one resolve ever rather than one per start.
"""

import asyncio
from dataclasses import dataclass, field

import structlog
from telethon import utils
from telethon.errors import (
    ChannelPrivateError,
    ChannelInvalidError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser, PeerChannel, PeerChat

from .channel_cache import (
    STATUS_NOT_FOUND,
    STATUS_NOT_JOINED,
    STATUS_OK,
    ChannelCache,
    make_entry,
    normalize_key,
)

log = structlog.get_logger(__name__)

# Telegram's way of saying "this username doesn't exist / isn't reachable".
# ValueError is included because Telethon itself raises it (rather than an RPC
# error) when a username simply doesn't resolve.
_NOT_FOUND_ERRORS = (UsernameNotOccupiedError, UsernameInvalidError, ChannelInvalidError, ValueError)


@dataclass(frozen=True)
class ResolvedChannel:
    """A channel this account can actually monitor."""

    key: str
    peer_id: int
    access_hash: int | None = None
    title: str | None = None

    def to_input_peer(self):
        """An input peer usable for API calls (channel_rater's
        iter_messages) without triggering a resolve: Telethon's
        get_input_entity returns an InputPeer* unchanged.

        Falls back to the plain marked id when no access_hash was cached -
        Telethon then looks it up in its own session cache, which it has for
        any channel this account is a member of.
        """
        if self.access_hash is None:
            return self.peer_id
        real_id, peer_cls = utils.resolve_id(self.peer_id)
        if peer_cls is PeerChannel:
            return InputPeerChannel(real_id, self.access_hash)
        if peer_cls is PeerChat:
            return InputPeerChat(real_id)
        return InputPeerUser(real_id, self.access_hash)


@dataclass
class ResolveOutcome:
    channels: list[ResolvedChannel] = field(default_factory=list)
    from_cache: int = 0
    resolved_now: int = 0
    not_joined: int = 0
    not_found: int = 0
    #: Entries never attempted because resolution was stopped early.
    unresolved: int = 0
    #: Human-readable reason resolution stopped early, if it did.
    aborted_reason: str | None = None

    @property
    def peer_ids(self) -> list[int]:
        """What `events.NewMessage(chats=...)` wants. Marked ids are matched
        verbatim by Telethon without any network call."""
        return [channel.peer_id for channel in self.channels]

    def summary_line(self) -> str:
        parts = [f"Мониторю {len(self.channels)} каналов"]
        skipped = []
        if self.not_joined:
            skipped.append(f"{self.not_joined} не подписан")
        if self.not_found:
            skipped.append(f"{self.not_found} не найдено")
        if self.unresolved:
            skipped.append(f"{self.unresolved} не резолвились")
        if skipped:
            parts.append("пропущено: " + ", ".join(skipped))
        parts.append(f"из кэша: {self.from_cache}, резолв сейчас: {self.resolved_now}")
        return " | ".join(parts)


class _ResolveAborted(Exception):
    """Raised internally when a FloodWait exceeds the configured ceiling."""

    def __init__(self, seconds: int):
        super().__init__(f"FloodWait of {seconds}s exceeds the configured ceiling")
        self.seconds = seconds


class ChannelResolver:
    def __init__(self, client, settings, cache: ChannelCache):
        self._client = client
        self._settings = settings
        self._cache = cache

    async def resolve(self, entries) -> ResolveOutcome:
        outcome = ResolveOutcome()
        force = bool(getattr(self._settings, "force_resolve", False))
        ttl_days = self._settings.cache_ttl_days

        pending = []
        for entry in entries:
            key = normalize_key(entry.target)
            cached = None if force else self._cache.fresh(key, ttl_days)
            if cached is None:
                pending.append((key, entry))
            else:
                outcome.from_cache += 1
                self._apply(outcome, key, cached)

        if not pending:
            log.info(
                "resolver.fully_cached",
                channels=len(outcome.channels),
                hint="no ResolveUsername calls made",
            )
            return outcome

        log.info(
            "resolver.resolving",
            to_resolve=len(pending),
            cached=outcome.from_cache,
            forced=force,
            delay_seconds=self._settings.resolve_delay_seconds,
        )

        joined_peer_ids = await self._joined_peer_ids()

        aborted_at = None
        for index, (key, entry) in enumerate(pending):
            if index:
                await asyncio.sleep(self._settings.resolve_delay_seconds)
            try:
                cache_entry = await self._resolve_one(entry, joined_peer_ids)
            except _ResolveAborted as abort:
                aborted_at = index
                outcome.aborted_reason = (
                    f"Telegram asked for a {abort.seconds}s FloodWait, over the "
                    f"MAX_FLOOD_WAIT_SECONDS={self._settings.max_flood_wait_seconds} ceiling"
                )
                log.warning(
                    "resolver.stopped_on_flood_wait",
                    seconds=abort.seconds,
                    ceiling=self._settings.max_flood_wait_seconds,
                    resolved_before_stopping=outcome.resolved_now,
                    remaining=len(pending) - index,
                    action="keeping what was resolved and continuing with those channels",
                    hint="re-run later to resolve the rest; the cache keeps this run's progress",
                )
                break

            outcome.resolved_now += 1
            self._cache.put(key, cache_entry)
            # Saved per entry, not at the end: a run stopped by a FloodWait
            # (or Ctrl+C) must not throw away resolves it already paid for.
            self._cache.save()
            self._apply(outcome, key, cache_entry)

        if aborted_at is not None:
            outcome.unresolved = len(pending) - aborted_at

        return outcome

    def _apply(self, outcome: ResolveOutcome, key: str, entry: dict) -> None:
        status = entry.get("status")
        if status == STATUS_OK and entry.get("id") is not None:
            outcome.channels.append(ResolvedChannel(
                key=key,
                peer_id=entry["id"],
                access_hash=entry.get("access_hash"),
                title=entry.get("title"),
            ))
        elif status == STATUS_NOT_JOINED:
            outcome.not_joined += 1
        elif status == STATUS_NOT_FOUND:
            outcome.not_found += 1

    async def _resolve_one(self, entry, joined_peer_ids: set[int] | None) -> dict:
        target = entry.target
        try:
            entity = await self._get_entity(target)
        except ChannelPrivateError:
            # Private channel this account isn't in - a real answer, and one
            # worth remembering so it isn't asked again every start.
            log.info("resolver.not_joined", target=target, reason="private")
            return make_entry(status=STATUS_NOT_JOINED)
        except _NOT_FOUND_ERRORS as exc:
            log.info("resolver.not_found", target=target, error=str(exc))
            return make_entry(status=STATUS_NOT_FOUND)
        except (_ResolveAborted, FloodWaitError):
            # MUST come before the catch-all below. _ResolveAborted is an
            # ordinary Exception, so a bare `except Exception` swallows it and
            # turns "stop, the account is throttled" into "this one channel is
            # not_found" - which then keeps resolving into the same limit.
            raise
        except Exception as exc:  # noqa: BLE001 - one odd channel must not stop the pass
            log.error("resolver.failed", target=target, error=str(exc))
            return make_entry(status=STATUS_NOT_FOUND)

        peer_id = utils.get_peer_id(entity)
        title = getattr(entity, "title", None) or getattr(entity, "username", None)

        # Resolving a PUBLIC channel succeeds whether or not we're a member,
        # so success alone doesn't mean we'll receive its updates. Membership
        # comes from the dialog list (one request for all of them).
        if joined_peer_ids is not None and peer_id not in joined_peer_ids:
            log.info("resolver.not_joined", target=target, title=title, reason="not in dialogs")
            return make_entry(
                status=STATUS_NOT_JOINED,
                id=peer_id,
                access_hash=getattr(entity, "access_hash", None),
                title=title,
            )

        return make_entry(
            status=STATUS_OK,
            id=peer_id,
            access_hash=getattr(entity, "access_hash", None),
            title=title,
        )

    async def _get_entity(self, target):
        """One resolve, obeying a FloodWait once. A second FloodWait in a row
        means the account is genuinely rate-limited, so we stop rather than
        keep poking it."""
        try:
            return await self._client.get_entity(target)
        except FloodWaitError as exc:
            self._raise_if_over_ceiling(exc)
            log.warning(
                "resolver.flood_wait",
                seconds=exc.seconds,
                target=target,
                action="waiting as instructed by Telegram",
            )
            await asyncio.sleep(exc.seconds)
            try:
                return await self._client.get_entity(target)
            except FloodWaitError as retry_exc:
                # Escalating limit - treat any repeat as a stop signal.
                raise _ResolveAborted(retry_exc.seconds) from retry_exc

    def _raise_if_over_ceiling(self, exc: FloodWaitError) -> None:
        if exc.seconds > self._settings.max_flood_wait_seconds:
            raise _ResolveAborted(exc.seconds) from exc

    async def _joined_peer_ids(self) -> set[int] | None:
        """Marked ids of every chat this account is actually in - ONE request
        that answers "am I subscribed?" for all 98 channels at once, instead
        of a per-channel check.

        Returns None if the list couldn't be fetched, which is treated as
        "membership unknown": nothing gets marked not_joined on a guess.
        """
        try:
            dialogs = await self._client.get_dialogs(limit=None)
        except FloodWaitError as exc:
            log.warning(
                "resolver.flood_wait_on_dialogs",
                seconds=exc.seconds,
                action="skipping the membership check this run",
            )
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("resolver.dialogs_failed", error=str(exc))
            return None

        peer_ids = set()
        for dialog in dialogs:
            entity = getattr(dialog, "entity", None)
            if entity is None:
                continue
            try:
                peer_ids.add(utils.get_peer_id(entity))
            except Exception:  # noqa: BLE001 - a weird dialog shouldn't break the set
                continue
        log.info("resolver.joined_chats", count=len(peer_ids))
        return peer_ids
