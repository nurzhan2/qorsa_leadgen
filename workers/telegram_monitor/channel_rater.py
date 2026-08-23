"""Standalone diagnostic tool - NOT part of the live monitor - that reads
each channel's recent post history via Telethon (the same account/session
monitor.py uses) and rates how often it actually surfaces real one-off
orders vs. hiring/vacancy noise vs. irrelevant chatter, so you can decide
which entries in channels.yml are worth keeping.

Run it occasionally (not continuously): `python -m workers.telegram_monitor.channel_rater`
from the repo root. See README.md "Оценка каналов" for how to read the
output and act on it.
"""

import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field

import structlog
from telethon import TelegramClient
from telethon.errors import FloodWaitError

from .config import ChannelEntry, MODULE_DIR, Settings, load_channels, load_keywords
from .matcher import Matcher

log = structlog.get_logger(__name__)

REPORT_PATH = MODULE_DIR / "channel_report.csv"

HOLD_THRESHOLD_PCT = 15.0
WEAK_THRESHOLD_PCT = 5.0


@dataclass
class ChannelRating:
    identifier: str
    subscribed: bool = False
    total_posts: int = 0
    orders: int = 0
    hiring: int = 0
    noise: int = 0
    # Per-channel matched keywords, kept only for optional debugging - not
    # written to the CSV (task's column list doesn't include it), but
    # useful if you want to inspect *why* a channel scored the way it did.
    sample_order_keywords: list[str] = field(default_factory=list)

    @property
    def order_rate(self) -> float:
        return (self.orders / self.total_posts) if self.total_posts else 0.0

    @property
    def verdict(self) -> str:
        # Beyond the three tiers asked for: a channel we couldn't read at
        # all (not subscribed, or zero posts came back) gets its own
        # verdict rather than a misleading "ВЫКИНУТЬ" - that label should
        # mean "we checked, it's low-signal", not "we couldn't check".
        if not self.subscribed or self.total_posts == 0:
            return "НЕДОСТУПЕН"
        rate_pct = self.order_rate * 100
        if rate_pct >= HOLD_THRESHOLD_PCT:
            return "ДЕРЖАТЬ"
        if rate_pct >= WEAK_THRESHOLD_PCT:
            return "СЛАБО"
        return "ВЫКИНУТЬ"


class ChannelRater:
    def __init__(self, settings: Settings, matcher: Matcher, client: TelegramClient):
        self._settings = settings
        self._matcher = matcher
        self._client = client

    async def rate_channel(self, entry: ChannelEntry) -> ChannelRating:
        identifier = str(entry.target)
        rating = ChannelRating(identifier=identifier)

        entity = await self._resolve(entry, identifier)
        if entity is None:
            return rating  # subscribed stays False - logged already in _resolve

        rating.subscribed = True

        try:
            async for message in self._client.iter_messages(entity, limit=self._settings.rate_sample):
                text = message.raw_text or ""
                rating.total_posts += 1
                result = self._matcher.match(text)
                if result.matched:
                    rating.orders += 1
                    rating.sample_order_keywords.extend(result.matched_intent + result.matched_order)
                elif result.rejected_reason:
                    rating.hiring += 1
                else:
                    rating.noise += 1
        except FloodWaitError as exc:
            log.warning("channel_rater.flood_wait_on_history", seconds=exc.seconds, target=identifier)
            await asyncio.sleep(exc.seconds)
            # Don't retry the whole history read - keep whatever was
            # counted so far rather than doubling up or losing the channel
            # entirely over one rate limit.
        except Exception as exc:  # noqa: BLE001 - one bad channel must never stop the whole rating run
            log.error("channel_rater.history_read_failed", target=identifier, error=str(exc))

        return rating

    async def _resolve(self, entry: ChannelEntry, identifier: str):
        try:
            return await self._client.get_entity(entry.target)
        except FloodWaitError as exc:
            log.warning("channel_rater.flood_wait_on_resolve", seconds=exc.seconds, target=identifier)
            await asyncio.sleep(exc.seconds)
            try:
                return await self._client.get_entity(entry.target)
            except Exception as exc2:  # noqa: BLE001
                log.error("channel_rater.not_accessible", target=identifier, error=str(exc2))
                return None
        except Exception as exc:  # noqa: BLE001 - private/deleted/renamed channel, wrong username, ...
            log.error("channel_rater.not_accessible", target=identifier, error=str(exc))
            return None

    async def rate_all(self, entries: list[ChannelEntry]) -> list[ChannelRating]:
        ratings = []
        for entry in entries:
            rating = await self.rate_channel(entry)
            ratings.append(rating)
            log.info(
                "channel_rater.channel_done",
                target=rating.identifier,
                subscribed=rating.subscribed,
                total=rating.total_posts,
                orders=rating.orders,
                hiring=rating.hiring,
                noise=rating.noise,
                order_rate_pct=round(rating.order_rate * 100, 1),
                verdict=rating.verdict,
            )
            await asyncio.sleep(self._settings.rate_channel_delay_seconds)
        return ratings


def print_report(ratings: list[ChannelRating]) -> None:
    columns = ("канал", "подписан", "total", "orders", "hiring", "noise", "rate%", "вердикт")
    widths = (30, 9, 6, 7, 7, 6, 7, 10)
    header = "  ".join(col.ljust(w) for col, w in zip(columns, widths))
    print(header)
    print("-" * len(header))
    for rating in sorted(ratings, key=lambda r: r.order_rate, reverse=True):
        row = (
            rating.identifier[:30].ljust(widths[0]),
            ("да" if rating.subscribed else "нет").ljust(widths[1]),
            str(rating.total_posts).ljust(widths[2]),
            str(rating.orders).ljust(widths[3]),
            str(rating.hiring).ljust(widths[4]),
            str(rating.noise).ljust(widths[5]),
            f"{rating.order_rate * 100:.1f}".ljust(widths[6]),
            rating.verdict,
        )
        print("  ".join(row))


def write_csv_report(path, ratings: list[ChannelRating]) -> None:
    # utf-8-sig (BOM) so Excel opens the Cyrillic verdicts/headers correctly
    # instead of mangling them - a bare utf-8 file looks fine in a text
    # editor but garbles in Excel on Windows without the BOM.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["username", "подписан", "total", "orders", "hiring", "noise", "order_rate%", "вердикт"])
        for rating in ratings:
            writer.writerow([
                rating.identifier,
                "да" if rating.subscribed else "нет",
                rating.total_posts,
                rating.orders,
                rating.hiring,
                rating.noise,
                f"{rating.order_rate * 100:.1f}",
                rating.verdict,
            ])


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
    )


async def _run() -> None:
    settings = Settings()
    _configure_logging(settings.log_level)

    channels = load_channels(settings.channels_file).channels
    keywords = load_keywords(settings.keywords_file)
    matcher = Matcher(keywords)

    log.info("channel_rater.starting", channels=len(channels), sample=settings.rate_sample)

    client = TelegramClient(settings.tg_session, settings.tg_api_id, settings.tg_api_hash)
    await client.start()
    try:
        rater = ChannelRater(settings, matcher, client)
        ratings = await rater.rate_all(channels)
    finally:
        if client.is_connected():
            await client.disconnect()

    print()
    print_report(ratings)
    write_csv_report(REPORT_PATH, ratings)
    log.info("channel_rater.done", channels=len(ratings), report=str(REPORT_PATH))


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
