"""Tests for hh_client.py via a mocked httpx transport - no real HH calls.

Covers the two things most likely to bite in production: HH's 2000-result
depth cap, and the auth/rate-limit error split.
"""

import httpx
import pytest

from workers.hh.hh_client import (
    MAX_RESULT_DEPTH,
    HhAuthError,
    HhClient,
    max_page,
)
from workers.hh.tests.fixtures import EMPLOYER_DETAIL, SEARCH_RESPONSE, vacancy

# Production backoff is 2..60s; these tests assert WHICH requests happen,
# not how long we wait between them.
FAST = {"retry_attempts": 3, "retry_base_seconds": 0.001, "retry_max_seconds": 0.002}


def make(handler, **kwargs):
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return http_client, HhClient(http_client, user_agent="test-agent",
                                 request_delay_seconds=0, **{**FAST, **kwargs})


# --- HH's documented depth cap -------------------------------------------


def test_max_page_matches_hh_documented_2000_result_depth():
    """HH: "глубина возвращаемых результатов не может быть больше 2000"."""
    assert MAX_RESULT_DEPTH == 2000
    assert max_page(100) == 19    # results 1901..2000; page 20 would error
    assert max_page(50) == 39
    assert max_page(10) == 199    # HH's own example: per_page=10&page=199 ok


def test_max_page_clamps_per_page_to_hh_maximum():
    # per_page above 100 is not accepted by HH; treat it as 100.
    assert max_page(1000) == max_page(100)


@pytest.mark.asyncio
async def test_pagination_stops_at_the_depth_cap_not_at_a_400():
    """Walking into page 20 would be an error response; the client must
    stop on its own rather than learning it the hard way."""
    pages_requested = []

    def handler(request):
        pages_requested.append(int(dict(request.url.params)["page"]))
        return httpx.Response(200, json={**SEARCH_RESPONSE, "pages": 999,
                                         "items": [vacancy(vacancy_id="x")]})

    http_client, client = make(handler, per_page=100)
    async with http_client:
        await client.iter_vacancies("python", "1", max_pages=50)

    assert max(pages_requested) == 19
    assert len(pages_requested) == 20


@pytest.mark.asyncio
async def test_pagination_respects_max_pages_when_lower_than_the_cap():
    pages = []

    def handler(request):
        pages.append(int(dict(request.url.params)["page"]))
        return httpx.Response(200, json={**SEARCH_RESPONSE, "pages": 999,
                                         "items": [vacancy()]})

    http_client, client = make(handler, per_page=100)
    async with http_client:
        await client.iter_vacancies("python", "1", max_pages=3)

    assert pages == [0, 1, 2]


@pytest.mark.asyncio
async def test_pagination_stops_when_hh_reports_fewer_pages():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={**SEARCH_RESPONSE, "pages": 2,
                                         "items": [vacancy()]})

    http_client, client = make(handler)
    async with http_client:
        await client.iter_vacancies("python", "1", max_pages=10)

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_pagination_stops_on_an_empty_page():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={**SEARCH_RESPONSE, "pages": 99, "items": []})

    http_client, client = make(handler)
    async with http_client:
        items = await client.iter_vacancies("python", "1", max_pages=10)

    assert len(calls) == 1
    assert items == []


# --- request shape --------------------------------------------------------


@pytest.mark.asyncio
async def test_sends_user_agent_and_search_params():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("User-Agent")
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=SEARCH_RESPONSE)

    http_client, client = make(handler, per_page=100)
    async with http_client:
        await client.search_vacancies("веб-дизайнер", "2", page=1)

    assert seen["ua"] == "test-agent"
    assert seen["params"]["text"] == "веб-дизайнер"
    assert seen["params"]["area"] == "2"
    assert seen["params"]["page"] == "1"
    assert seen["params"]["per_page"] == "100"
    assert seen["auth"] is None  # no token configured


@pytest.mark.asyncio
async def test_token_is_sent_as_a_bearer_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=SEARCH_RESPONSE)

    http_client, client = make(handler, token="SECRET123")
    async with http_client:
        await client.search_vacancies("python", "1")

    assert seen["auth"] == "Bearer SECRET123"
    assert client.has_token is True


# --- errors ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_403_raises_auth_error_without_retrying():
    """This is the real-world failure: no token -> 403. Retrying can't fix
    credentials, and hammering an endpoint that just refused is what gets an
    IP blocked."""
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403, json={"errors": [{"type": "forbidden"}]})

    http_client, client = make(handler)
    async with http_client:
        with pytest.raises(HhAuthError):
            await client.search_vacancies("python", "1")

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_auth_error_propagates_out_of_pagination():
    """Every later request would fail identically, so the run must be able
    to stop rather than grind through the whole grid."""
    def handler(request):
        return httpx.Response(403, json={"errors": [{"type": "forbidden"}]})

    http_client, client = make(handler)
    async with http_client:
        with pytest.raises(HhAuthError):
            await client.iter_vacancies("python", "1", max_pages=5)


@pytest.mark.asyncio
async def test_429_is_retried_then_succeeds():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, json={"errors": [{"type": "rate_limited"}]})
        return httpx.Response(200, json=SEARCH_RESPONSE)

    http_client, client = make(handler)
    async with http_client:
        payload = await client.search_vacancies("python", "1")

    assert attempts["n"] == 3
    assert payload["found"] == 1
    assert client.rate_limit_hits == 2


@pytest.mark.asyncio
async def test_500_is_retried():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=SEARCH_RESPONSE)

    http_client, client = make(handler)
    async with http_client:
        await client.search_vacancies("python", "1")

    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_a_bad_query_ends_that_keyword_without_killing_the_run():
    def handler(request):
        return httpx.Response(400, json={"errors": [{"type": "bad_argument"}]})

    http_client, client = make(handler)
    async with http_client:
        items = await client.iter_vacancies("python", "1", max_pages=3)

    assert items == []


# --- employers ------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_employer_returns_site_url():
    def handler(request):
        assert request.url.path == "/employers/1455"
        return httpx.Response(200, json=EMPLOYER_DETAIL)

    http_client, client = make(handler)
    async with http_client:
        details = await client.fetch_employer("1455")

    assert details["site_url"] == "https://hh.ru"
    assert details["type"] == "company"


@pytest.mark.asyncio
async def test_fetch_employer_failure_is_not_fatal():
    """The lead is still usable without a domain."""
    def handler(request):
        return httpx.Response(404, json={"errors": [{"type": "not_found"}]})

    http_client, client = make(handler)
    async with http_client:
        assert await client.fetch_employer("nope") is None


@pytest.mark.asyncio
async def test_fetch_employer_with_a_blank_id_makes_no_request():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=EMPLOYER_DETAIL)

    http_client, client = make(handler)
    async with http_client:
        assert await client.fetch_employer("") is None

    assert calls == []
