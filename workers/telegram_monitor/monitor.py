"""Telethon-driven monitor: listens to new messages in the configured
channels/chats, matches them as order posts, extracts a contact, and hands
the resulting lead to CoreClient.

Scope, by design: this only subscribes to `events.NewMessage` on chats the
account has already joined by hand. It never calls anything like
`get_participants`, never lists subscribers, and never reads comment
threads under a post - only the message stream itself. See README.md.
"""

import asyncio

import structlog
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from .channel_cache import load_cache
from .config import ChannelsConfig, KeywordsConfig, Settings
from .core_client import CoreClient, RawCompanyRequest
from .extractor import extract_contact
from .matcher import Matcher
from .resolver import ChannelResolver

log = structlog.get_logger(__name__)

RAW_TEXT_PREVIEW_LEN = 500
# Small pause after each send so a burst of matching posts doesn't hammer
# the core; this is a "be a good citizen" throttle, not a hard rate limiter.
POST_SEND_DELAY_SECONDS = 0.5


class TelegramOrderMonitor:
    def __init__(self, settings: Settings, channels: ChannelsConfig, keywords: KeywordsConfig):
        self._settings = settings
        self._channels_config = channels
        self._matcher = Matcher(keywords)
        self._core_client = CoreClient(settings.core_url)
        self._client = TelegramClient(settings.tg_session, settings.tg_api_id, settings.tg_api_hash)

        # In-memory, per-run only - fine for a long-lived process, resets on
        # restart (a restart re-scanning a handful of recent posts is
        # harmless: the core's own dedup collapses repeats by domain/phone/
        # fuzzy name anyway).
        self._seen_posts: set[tuple[int, int]] = set()
        self._seen_contacts: set[str] = set()

    async def start(self) -> None:
        await self._client.start()
        log.info("monitor.starting", configured_channels=len(self._channels_config.channels))

        outcome = await self._resolve_channels()
        if not outcome.channels:
            log.warning(
                "monitor.no_channels_resolved",
                hint="check channels.yml, or run: python -m workers.telegram_monitor.channel_cache --report",
            )

        # Marked peer ids, not entities: Telethon matches a negative id
        # verbatim when building the event filter, so nothing here triggers a
        # resolve. Passing entity objects would send us back to square one.
        self._client.add_event_handler(
            self._on_message, events.NewMessage(chats=outcome.peer_ids))

        print(outcome.summary_line())
        log.info(
            "monitor.listening",
            monitored=len(outcome.channels),
            from_cache=outcome.from_cache,
            resolved_now=outcome.resolved_now,
            skipped_not_joined=outcome.not_joined,
            skipped_not_found=outcome.not_found,
        )
        if outcome.aborted_reason:
            log.warning("monitor.partial_channel_set", reason=outcome.aborted_reason)

        await self._client.run_until_disconnected()

    async def close(self) -> None:
        await self._core_client.aclose()
        if self._client.is_connected():
            await self._client.disconnect()

    async def _resolve_channels(self):
        """Cache-first: with a warm channel_cache.json this makes no
        ResolveUsername calls at all. See resolver.py for the full policy."""
        cache = load_cache(self._settings.channel_cache_file)
        resolver = ChannelResolver(self._client, self._settings, cache)
        return await resolver.resolve(self._channels_config.channels)

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        match_result = self._matcher.match(text)
        if not match_result.matched:
            if match_result.rejected_reason:
                # A vacancy/hiring post that would otherwise have matched -
                # never sent to the core, just visible when tuning keywords.
                log.debug(
                    "monitor.post_rejected",
                    reason=match_result.rejected_reason,
                    anti=match_result.matched_anti,
                    order=match_result.matched_order,
                    preview=text[:120],
                )
            return

        post_key = (event.chat_id, event.id)
        if post_key in self._seen_posts:
            return
        self._seen_posts.add(post_key)

        chat = await self._safe_get_chat(event)
        sender_username = await self._safe_get_sender_username(event)

        contact = extract_contact(text, sender_username)
        contact_key = contact.messenger or contact.phone or contact.email
        if contact_key and contact_key in self._seen_contacts:
            log.info("monitor.duplicate_contact_skipped", contact=contact_key)
            return
        if contact_key:
            self._seen_contacts.add(contact_key)

        channel_label = _channel_label(chat, event.chat_id)
        source_url = _build_source_url(chat, event.chat_id, event.id)

        lead = RawCompanyRequest(
            name=f"Заявка из {channel_label}",
            messenger=contact.messenger,
            phone=contact.phone,
            email=contact.email,
            city=None,
            source="TELEGRAM_ORDER",
            source_url=source_url,
            has_site=True,
            raw={
                "text": text[:RAW_TEXT_PREVIEW_LEN],
                "category": match_result.category,
                "budgetMentioned": match_result.budget_flag,
                "channel": channel_label,
                "matched_weight": match_result.weight,
                "no_direct_contact": contact.no_direct_contact,
                # Always true here (a rejected/non-matching post already
                # returned above) - kept explicit so anyone inspecting a
                # lead's raw data downstream doesn't have to infer it, and
                # so this stays meaningful if the match/reject logic ever
                # grows a matched-but-not-an-order case in the future.
                "is_order": match_result.is_order,
                "matched_order": match_result.matched_order,
            },
        )

        log.info(
            "monitor.lead_matched",
            channel=channel_label,
            category=match_result.category,
            weight=match_result.weight,
            budget=match_result.budget_flag,
            contact=contact_key,
            no_direct_contact=contact.no_direct_contact,
        )

        await self._core_client.send_lead(lead)
        await asyncio.sleep(POST_SEND_DELAY_SECONDS)

    async def _safe_get_chat(self, event: events.NewMessage.Event):
        try:
            return await event.get_chat()
        except FloodWaitError as exc:
            log.warning("monitor.flood_wait_on_get_chat", seconds=exc.seconds)
            await asyncio.sleep(exc.seconds)
            return await event.get_chat()

    async def _safe_get_sender_username(self, event: events.NewMessage.Event) -> str | None:
        try:
            sender = await event.get_sender()
        except FloodWaitError as exc:
            log.warning("monitor.flood_wait_on_get_sender", seconds=exc.seconds)
            await asyncio.sleep(exc.seconds)
            try:
                sender = await event.get_sender()
            except Exception:  # noqa: BLE001 - contact extraction has its own fallback
                return None
        except Exception:  # noqa: BLE001
            return None
        return getattr(sender, "username", None)


def _channel_label(chat, fallback_id: int) -> str:
    username = getattr(chat, "username", None)
    if username:
        return f"@{username}"
    title = getattr(chat, "title", None)
    return title or str(fallback_id)


def _build_source_url(chat, chat_id: int, message_id: int) -> str:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    # Private supergroup/channel: Telegram's internal link format works for
    # anyone already a member (which this worker always is, by design).
    internal_id = str(chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal_id}/{message_id}"
