"""Unit tests for staleness.py - the stale-vacancy detector this worker is
built around. Pure date arithmetic, no network.

`now` is always passed explicitly: a test whose result depends on the wall
clock passes today and fails in six weeks.
"""

from datetime import datetime, timedelta, timezone

import pytest

from workers.hh.staleness import age_days, is_stale, parse_published_at
from workers.hh.tests.fixtures import VACANCY_ITEM

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


# --- parse_published_at ---------------------------------------------------


def test_parses_hh_timestamp_with_colonless_offset():
    """HH returns "+0400", not "+04:00" - this is the exact string from
    HH's own published example."""
    parsed = parse_published_at(VACANCY_ITEM["published_at"])

    assert parsed is not None
    assert parsed.year == 2013 and parsed.month == 10 and parsed.day == 11
    assert parsed.utcoffset() == timedelta(hours=4)


def test_parses_offset_with_colon_too():
    assert parse_published_at("2026-08-01T10:00:00+03:00") is not None


def test_naive_timestamp_is_treated_as_utc_not_rejected():
    parsed = parse_published_at("2026-08-01T10:00:00")

    assert parsed is not None
    assert parsed.tzinfo is not None


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-date", 12345, {}, "2026-13-45"])
def test_unusable_values_return_none_rather_than_raising(value):
    assert parse_published_at(value) is None


# --- age_days -------------------------------------------------------------


def test_age_days_counts_whole_days():
    published = (NOW - timedelta(days=10, hours=3)).isoformat()

    assert age_days(published, now=NOW) == 10


def test_age_days_of_something_published_today_is_zero():
    assert age_days(NOW.isoformat(), now=NOW) == 0


def test_age_days_is_none_when_the_date_is_unusable():
    assert age_days(None, now=NOW) is None
    assert age_days("garbage", now=NOW) is None


def test_future_publication_clamps_to_zero_not_negative():
    """A negative age would sort as the STALEST thing in the batch and hand
    dedup.py the wrong representative vacancy."""
    future = (NOW + timedelta(days=5)).isoformat()

    assert age_days(future, now=NOW) == 0


def test_age_days_respects_the_timezone_offset():
    # Same instant, different offsets - the ages must agree.
    utc = "2026-08-20T09:00:00+00:00"
    msk = "2026-08-20T12:00:00+0300"

    assert age_days(utc, now=NOW) == age_days(msk, now=NOW)


# --- is_stale -------------------------------------------------------------


@pytest.mark.parametrize(("age", "expected"), [
    (0, False), (1, False), (44, False),
    (45, True),      # exactly at the threshold counts as stale
    (46, True), (400, True),
])
def test_is_stale_at_the_default_threshold(age, expected):
    assert is_stale(age, stale_days=45) is expected


def test_unknown_age_is_never_stale():
    """We don't invent heat we can't evidence."""
    assert is_stale(None, stale_days=45) is False


def test_threshold_is_configurable():
    assert is_stale(20, stale_days=14) is True
    assert is_stale(20, stale_days=30) is False


def test_end_to_end_on_a_real_vacancy_payload():
    """The stale path, from HH's published payload shape to the verdict."""
    fresh = dict(VACANCY_ITEM, published_at=(NOW - timedelta(days=3)).isoformat())
    rotten = dict(VACANCY_ITEM, published_at=(NOW - timedelta(days=90)).isoformat())

    fresh_age = age_days(fresh["published_at"], now=NOW)
    rotten_age = age_days(rotten["published_at"], now=NOW)

    assert is_stale(fresh_age, 45) is False
    assert is_stale(rotten_age, 45) is True
    # A 2013 vacancy from HH's own example is very stale indeed.
    assert is_stale(age_days(VACANCY_ITEM["published_at"], now=NOW), 45) is True
