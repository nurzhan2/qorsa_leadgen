"""Stale-vacancy detection - the reason this worker exists.

A company posting a developer/designer vacancy has an approved budget and a
need right now. If that vacancy is STILL open 45+ days later, they have been
unable to hire: the salary is off, the role is hard to fill, or nobody wants
it. That is the moment outsourcing stops being a fallback and starts being
the plan. A stale vacancy is therefore a HOTTER lead than a fresh one, not a
colder one.

Pure date arithmetic - no network, no HH import.
"""

from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)


def parse_published_at(value) -> datetime | None:
    """HH returns e.g. "2013-10-11T13:27:16+0400" - ISO 8601 with a
    *colon-less* UTC offset. Python's datetime.fromisoformat handles that
    form from 3.11 onwards (this project is on 3.12); on older versions it
    would raise, which is why this is guarded rather than assumed.

    Returns an aware datetime, or None for anything unparseable - a missing
    date must not crash a whole page of results.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        log.debug("hh.unparseable_published_at", value=value[:40])
        return None
    if parsed.tzinfo is None:
        # Undocumented but possible; assume UTC rather than crashing on an
        # offset-naive/aware comparison later.
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_days(published_at, now: datetime | None = None) -> int | None:
    """Whole days since publication. None if the date is unusable.

    Clamped at 0: a vacancy published "in the future" (clock skew, or HH
    rounding a scheduled publication) is 0 days old, never negative - a
    negative age would sort as the stalest thing in the batch and pick the
    wrong representative vacancy in dedup.py.
    """
    published = parse_published_at(published_at)
    if published is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0, (reference - published).days)


def is_stale(age: int | None, stale_days: int) -> bool:
    """True when a vacancy has been open long enough to read as "can't
    hire". Unknown age is NOT stale - we don't invent heat we can't
    evidence."""
    if age is None:
        return False
    return age >= stale_days
