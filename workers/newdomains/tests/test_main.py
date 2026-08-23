"""Tests for the "no provider configured -> exit cleanly" behavior main.py
relies on. Deliberately does NOT call main._run() end-to-end: that would
construct Settings() from whatever real .env/.env vars happen to be
present at test time, and if a real DOMAINS_API_KEY is ever configured for
actual use, an end-to-end test could start making real, billed provider
API calls during `pytest`. Instead this locks down create_provider()'s
behavior directly, using explicit constructor overrides - pydantic-settings
gives those the highest precedence, so these are never affected by a real
.env file.
"""

from workers.newdomains.config import Settings
from workers.newdomains.providers import create_provider


def test_missing_everything_trips_the_exit_condition():
    settings = Settings(domains_provider=None, domains_api_key=None)

    assert create_provider(settings) is None


def test_blank_provider_name_also_trips_the_exit_condition():
    settings = Settings(domains_provider="", domains_api_key="some-key")

    assert create_provider(settings) is None


def test_fully_configured_whoisxml_does_not_trip_the_exit_condition():
    settings = Settings(domains_provider="whoisxml", domains_api_key="real-key-123")

    assert create_provider(settings) is not None
