"""Unit tests for providers.py - create_provider() selection logic (using
explicit Settings constructor overrides, never real env/.env - see
test_main.py for why that matters), plus the two concrete providers
against a mocked httpx transport. No real network requests."""

import httpx
import pytest

from workers.newdomains.config import Settings
from workers.newdomains.providers import (
    CustomHttpProvider,
    WhoisXmlNrdProvider,
    create_provider,
)

# --- create_provider(): selection logic ----------------------------------


def test_no_provider_configured_returns_none():
    settings = Settings(domains_provider=None, domains_api_key=None)

    assert create_provider(settings) is None


def test_provider_name_without_api_key_returns_none():
    settings = Settings(domains_provider="whoisxml", domains_api_key=None)

    assert create_provider(settings) is None


def test_api_key_without_provider_name_returns_none():
    settings = Settings(domains_provider=None, domains_api_key="some-key")

    assert create_provider(settings) is None


def test_whoisxml_provider_selected_with_key():
    settings = Settings(domains_provider="whoisxml", domains_api_key="my-key")

    provider = create_provider(settings)

    assert isinstance(provider, WhoisXmlNrdProvider)


def test_custom_provider_selected_with_key_and_url():
    settings = Settings(domains_provider="custom", domains_api_key="my-key", domains_api_url="https://feed.example/api")

    provider = create_provider(settings)

    assert isinstance(provider, CustomHttpProvider)


def test_custom_provider_without_url_returns_none():
    settings = Settings(domains_provider="custom", domains_api_key="my-key", domains_api_url=None)

    assert create_provider(settings) is None


def test_unknown_provider_name_returns_none():
    settings = Settings(domains_provider="something-else", domains_api_key="my-key")

    assert create_provider(settings) is None


def test_provider_name_is_case_insensitive():
    settings = Settings(domains_provider="WhoisXML", domains_api_key="my-key")

    assert isinstance(create_provider(settings), WhoisXmlNrdProvider)


# --- WhoisXmlNrdProvider: mocked transport -------------------------------


@pytest.mark.asyncio
async def test_whoisxml_provider_parses_new_registered_domains_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "newRegisteredDomains": [
                {"domainName": "freshbiz.ru", "date": "2026-08-20", "registrarName": "REG.RU"},
            ],
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WhoisXmlNrdProvider("fake-key", tlds=["ru"])
        records = await provider.fetch_new_domains(client)

    assert records == [{"domain": "freshbiz.ru", "registered_date": "2026-08-20", "registrar": "REG.RU"}]


@pytest.mark.asyncio
async def test_whoisxml_provider_falls_back_to_domains_list_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"domainsList": [{"domain": "other.com", "createdDate": "2026-08-21"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WhoisXmlNrdProvider("fake-key", tlds=["com"])
        records = await provider.fetch_new_domains(client)

    assert records == [{"domain": "other.com", "registered_date": "2026-08-21", "registrar": None}]


@pytest.mark.asyncio
async def test_whoisxml_provider_unrecognized_shape_yields_no_records_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"somethingElse": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WhoisXmlNrdProvider("fake-key", tlds=["ru"])
        records = await provider.fetch_new_domains(client)

    assert records == []


@pytest.mark.asyncio
async def test_whoisxml_provider_http_error_yields_no_records_not_a_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "invalid key"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WhoisXmlNrdProvider("bad-key", tlds=["ru"])
        records = await provider.fetch_new_domains(client)

    assert records == []


@pytest.mark.asyncio
async def test_whoisxml_provider_queries_each_configured_tld():
    seen_tlds = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_tlds.append(request.url.params.get("tlds"))
        return httpx.Response(200, json={"newRegisteredDomains": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = WhoisXmlNrdProvider("fake-key", tlds=["ru", "com"])
        await provider.fetch_new_domains(client)

    assert seen_tlds == ["ru", "com"]


# --- CustomHttpProvider: mocked transport --------------------------------


@pytest.mark.asyncio
async def test_custom_provider_parses_bare_array():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer my-key"
        return httpx.Response(200, json=[{"domain": "a.ru", "registered_date": "2026-08-20", "registrar": "X"}])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = CustomHttpProvider("my-key", "https://feed.example/api")
        records = await provider.fetch_new_domains(client)

    assert records == [{"domain": "a.ru", "registered_date": "2026-08-20", "registrar": "X"}]


@pytest.mark.asyncio
async def test_custom_provider_parses_domains_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"domains": [{"domain": "b.ru"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = CustomHttpProvider("my-key", "https://feed.example/api")
        records = await provider.fetch_new_domains(client)

    assert records == [{"domain": "b.ru", "registered_date": None, "registrar": None}]


@pytest.mark.asyncio
async def test_custom_provider_unrecognized_shape_yields_no_records():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = CustomHttpProvider("my-key", "https://feed.example/api")
        records = await provider.fetch_new_domains(client)

    assert records == []
