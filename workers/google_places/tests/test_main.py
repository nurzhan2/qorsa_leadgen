"""Tests for the "no API key -> exit cleanly, don't crash" behavior that
main.py relies on. Deliberately does NOT call main._run() end-to-end: that
would construct Settings() from whatever real .env/.env vars happen to be
present at test time, and if a real GOOGLE_PLACES_API_KEY is ever
configured for actual use, an end-to-end test would start making real,
billed API calls during `pytest`. Instead this locks down the exact
boolean condition main.py's early-exit check relies on
(`if not settings.google_places_api_key: ...`), using explicit
constructor overrides - pydantic-settings gives constructor kwargs the
highest precedence, so these are never affected by a real .env file.
"""

from workers.google_places.config import Settings


def test_missing_api_key_trips_the_exit_condition():
    settings = Settings(google_places_api_key=None)

    assert not settings.google_places_api_key


def test_blank_api_key_also_trips_the_exit_condition():
    """GOOGLE_PLACES_API_KEY= (empty string) in .env is just as "not
    configured" as leaving the line out entirely."""
    settings = Settings(google_places_api_key="")

    assert not settings.google_places_api_key


def test_real_api_key_does_not_trip_the_exit_condition():
    settings = Settings(google_places_api_key="real-key-123")

    assert settings.google_places_api_key
