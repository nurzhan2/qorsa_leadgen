"""Persistent cache of resolved Telegram channels, so the worker asks
Telegram to resolve a username exactly ONCE instead of on every start.

Why this exists: resolving 98 usernames on every startup is 98
`ResolveUsernameRequest` calls in a burst, and Telegram answers that with an
escalating `FloodWait` (3s, then 51s, then longer). The fix is not to work
around the limit - it's to stop making the calls. A channel's numeric id and
access_hash don't change, so one resolve is enough; everything after that is
served from this file.

Negative results are cached too (`not_found`, `not_joined`), which matters
just as much: without that, every dead or unsubscribed channel in
channels.yml would be re-resolved on every single start forever.

Deliberately pure - no Telethon import, no network. That keeps it trivially
testable and lets `--report` run without a Telegram session:

    python -m workers.telegram_monitor.channel_cache --report
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from .config import MODULE_DIR

log = structlog.get_logger(__name__)

CACHE_PATH = MODULE_DIR / "channel_cache.json"
DEFAULT_TTL_DAYS = 30

# Entry statuses. Anything not "ok" means "do not monitor this channel", and
# is remembered precisely so it isn't retried on the next run.
STATUS_OK = "ok"
STATUS_NOT_FOUND = "not_found"
STATUS_NOT_JOINED = "not_joined"
VALID_STATUSES = (STATUS_OK, STATUS_NOT_FOUND, STATUS_NOT_JOINED)

STATUS_EXPLANATIONS = {
    STATUS_OK: "resolved and joined - monitored",
    STATUS_NOT_FOUND: "no such username (renamed/deleted) - remove from channels.yml",
    STATUS_NOT_JOINED: "exists, but this account is not a member - join it, or remove it",
}


def normalize_key(target) -> str:
    """Cache key for a channels.yml entry. Telegram usernames are
    case-insensitive, so keys are lowercased - otherwise `@ForDev` and
    `@fordev` would each get their own entry and each get resolved.

    Whitespace is stripped BEFORE the "@" (and again after): on "  @ForDev  "
    an "@"-first lstrip hits a space, removes nothing, and leaves "@fordev" as
    a second key for a channel that's already cached.
    """
    return str(target).strip().lstrip("@").strip().lower()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_entry(
    *,
    status: str,
    id: int | None = None,  # noqa: A002 - matches the documented on-disk key
    access_hash: int | None = None,
    title: str | None = None,
    resolved_at: str | None = None,
) -> dict:
    """One cache record. `id` is the MARKED peer id (what
    `telethon.utils.get_peer_id` returns, e.g. -1001234567890) because that's
    the form `events.NewMessage(chats=...)` accepts directly without any
    network call - see resolver.py."""
    return {
        "id": id,
        "access_hash": access_hash,
        "title": title,
        "resolved_at": resolved_at or utc_now_iso(),
        "status": status,
    }


def _parse_timestamp(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # A timestamp written by an older/hand-edited file may be naive; treat it
    # as UTC rather than crashing on an offset-naive/aware comparison.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_stale(entry: dict | None, ttl_days: float = DEFAULT_TTL_DAYS) -> bool:
    """True when an entry should be re-resolved.

    Missing, malformed, or unparseable-timestamp entries count as stale: the
    safe direction to fail is "ask Telegram again", never "trust garbage".
    A ttl_days of 0 or less expires everything immediately.
    """
    if not isinstance(entry, dict):
        return True
    if entry.get("status") not in VALID_STATUSES:
        return True
    resolved_at = _parse_timestamp(entry.get("resolved_at"))
    if resolved_at is None:
        return True
    if ttl_days <= 0:
        return True
    return datetime.now(timezone.utc) - resolved_at > timedelta(days=ttl_days)


class ChannelCache:
    """The on-disk cache, loaded into memory. Not thread-safe; the worker is
    single-threaded asyncio."""

    def __init__(self, path: Path = CACHE_PATH, entries: dict | None = None):
        self.path = Path(path)
        self._entries: dict[str, dict] = dict(entries or {})

    # --- reads ---------------------------------------------------------

    def get(self, username) -> dict | None:
        return self._entries.get(normalize_key(username))

    def fresh(self, username, ttl_days: float = DEFAULT_TTL_DAYS) -> dict | None:
        """The entry for `username` if it's usable as-is, else None (meaning
        "resolve this one"). This is the single question the resolver asks."""
        entry = self.get(username)
        return None if is_stale(entry, ttl_days) else entry

    @property
    def entries(self) -> dict[str, dict]:
        return self._entries

    def counts_by_status(self) -> dict[str, int]:
        counts = {status: 0 for status in VALID_STATUSES}
        for entry in self._entries.values():
            status = entry.get("status")
            if status in counts:
                counts[status] += 1
        return counts

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, username) -> bool:
        return normalize_key(username) in self._entries

    # --- writes --------------------------------------------------------

    def put(self, username, entry: dict) -> None:
        self._entries[normalize_key(username)] = entry

    def save(self) -> None:
        save_cache(self)


def load_cache(path: Path = CACHE_PATH) -> ChannelCache:
    """Never raises. A missing file is simply an empty cache; a corrupt one is
    logged and treated as empty (the worst case is one extra resolve pass,
    which is strictly better than refusing to start)."""
    path = Path(path)
    if not path.exists():
        return ChannelCache(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("channel_cache.unreadable", path=str(path), error=str(exc))
        return ChannelCache(path)
    if not isinstance(data, dict):
        log.warning("channel_cache.unexpected_shape", path=str(path), type=type(data).__name__)
        return ChannelCache(path)
    entries = {normalize_key(k): v for k, v in data.items() if isinstance(v, dict)}
    return ChannelCache(path, entries)


def save_cache(cache: ChannelCache) -> None:
    """Atomic-ish write via a temp file + replace, so an interrupted save
    can't leave a half-written cache that the next run has to discard."""
    path = Path(cache.path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(cache.entries, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.error("channel_cache.save_failed", path=str(path), error=str(exc))


# --- `--report` ---------------------------------------------------------


def _age_label(entry: dict) -> str:
    resolved_at = _parse_timestamp(entry.get("resolved_at"))
    if resolved_at is None:
        return "?"
    days = (datetime.now(timezone.utc) - resolved_at).days
    if days <= 0:
        return "today"
    return f"{days}d ago"


def build_report_rows(cache: ChannelCache) -> list[tuple[str, str, str, str, str]]:
    """(username, status, resolved_at, age, title), worst status first so the
    channels worth deleting from channels.yml are at the top."""
    order = {STATUS_NOT_FOUND: 0, STATUS_NOT_JOINED: 1, STATUS_OK: 2}
    rows = []
    for username, entry in cache.entries.items():
        rows.append((
            username,
            str(entry.get("status") or "?"),
            str(entry.get("resolved_at") or "?")[:19],
            _age_label(entry),
            str(entry.get("title") or ""),
        ))
    rows.sort(key=lambda r: (order.get(r[1], 3), r[0]))
    return rows


def render_report(cache: ChannelCache) -> str:
    if not cache.entries:
        return (
            f"Cache is empty ({cache.path}).\n"
            "Nothing has been resolved yet - run the monitor once "
            "(python -m workers.telegram_monitor.main)."
        )

    rows = build_report_rows(cache)
    headers = ("username", "status", "resolved_at", "age", "title")
    widths = [
        max(len(headers[i]), max((len(r[i]) for r in rows), default=0))
        for i in range(len(headers))
    ]
    widths[4] = min(widths[4], 40)

    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    lines.append("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        cells = [row[i].ljust(widths[i]) for i in range(4)]
        cells.append(row[4][:40])
        lines.append("  ".join(cells))

    counts = cache.counts_by_status()
    lines.append("")
    lines.append(
        f"Total {len(cache)} cached: "
        f"{counts[STATUS_OK]} ok, "
        f"{counts[STATUS_NOT_JOINED]} not_joined, "
        f"{counts[STATUS_NOT_FOUND]} not_found"
    )
    lines.append("")
    for status in VALID_STATUSES:
        lines.append(f"  {status:<12} {STATUS_EXPLANATIONS[status]}")
    lines.append("")
    lines.append(f"Cache file: {cache.path}")
    lines.append(
        "Re-resolve everything with FORCE_RESOLVE=true, or by deleting the file above."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m workers.telegram_monitor.channel_cache",
        description="Inspect the resolved-channel cache (offline - no Telegram connection).",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the cached status of every channel, worst first",
    )
    parser.add_argument(
        "--path",
        default=str(CACHE_PATH),
        help=f"cache file to read (default: {CACHE_PATH})",
    )
    args = parser.parse_args(argv)

    if not args.report:
        parser.print_help()
        return 0

    # Channel titles are Cyrillic and a Russian-locale Windows console
    # defaults to cp866, which would raise UnicodeEncodeError on print().
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
        pass

    print(render_report(load_cache(Path(args.path))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
