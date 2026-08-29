"""Tests for OverpassClient's endpoint rotation, via a mocked httpx
transport - no real Overpass calls.

The behaviour under test: when one public mirror is persistently overloaded
(429/504), the worker moves to another rather than hammering the busy one.
Every attempt still goes through the full retry/backoff first, so we never
hop away at the first 429.
"""

import httpx
import pytest

from workers.osm.overpass_client import OverpassClient

BBOX = (55.5, 37.3, 55.9, 37.8)
A = "https://a.example/api/interpreter"
B = "https://b.example/api/interpreter"
C = "https://c.example/api/interpreter"

OK_BODY = {"elements": [{"type": "node", "id": 1, "tags": {"name": "Кафе Уют"}}]}


def client_with(handler, endpoints, **kwargs):
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return http_client, OverpassClient(
        http_client,
        user_agent="test-agent",
        request_delay_seconds=0,
        request_timeout_seconds=30,
        page_size=10,
        endpoints=endpoints,
        # Real production backoff is 5..120s over 5 attempts; at those values
        # a single "endpoint is busy" case would sleep for minutes. The
        # policy under test is WHICH endpoint gets used, not how long we wait.
        retry_attempts=kwargs.pop("retry_attempts", 3),
        retry_base_seconds=kwargs.pop("retry_base_seconds", 0.001),
        retry_max_seconds=kwargs.pop("retry_max_seconds", 0.002),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_uses_the_first_endpoint_when_it_works():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=OK_BODY)

    http_client, client = client_with(handler, [A, B])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    assert len(elements) == 1
    assert seen == [A]
    assert client.endpoint_switches == 0


@pytest.mark.asyncio
async def test_switches_to_the_next_endpoint_when_the_first_is_rate_limited():
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == A:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json=OK_BODY)

    http_client, client = client_with(handler, [A, B])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    assert len(elements) == 1
    assert client.endpoint == B
    assert client.endpoint_switches == 1
    # A got the full retry treatment before we gave up on it - we don't hop
    # away at the first 429.
    assert seen.count(A) > 1
    assert seen[-1] == B


@pytest.mark.asyncio
async def test_switches_on_504_as_well_as_429():
    def handler(request):
        if str(request.url) == A:
            return httpx.Response(504, text="Gateway Timeout")
        return httpx.Response(200, json=OK_BODY)

    http_client, client = client_with(handler, [A, B])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    assert len(elements) == 1
    assert client.endpoint == B


@pytest.mark.asyncio
async def test_walks_through_several_endpoints_until_one_answers():
    def handler(request):
        url = str(request.url)
        if url in (A, B):
            return httpx.Response(429, text="busy")
        return httpx.Response(200, json=OK_BODY)

    http_client, client = client_with(handler, [A, B, C])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    assert len(elements) == 1
    assert client.endpoint == C
    assert client.endpoint_switches == 2


@pytest.mark.asyncio
async def test_gives_up_with_an_empty_result_when_every_endpoint_is_busy():
    def handler(request):
        return httpx.Response(429, text="busy")

    http_client, client = client_with(handler, [A, B])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    # An empty list, not an exception: one bad combo must not end the run,
    # and the runner deliberately leaves it unmarked so a later run retries.
    assert elements == []


@pytest.mark.asyncio
async def test_rate_limit_hits_are_counted_per_endpoint():
    def handler(request):
        if str(request.url) == A:
            return httpx.Response(429, text="busy")
        return httpx.Response(200, json=OK_BODY)

    http_client, client = client_with(handler, [A, B])
    async with http_client:
        await client.fetch_elements(BBOX, "amenity", "cafe")

    assert client.rate_limit_hits[A] > 0
    assert client.rate_limit_hits[B] == 0


@pytest.mark.asyncio
async def test_a_malformed_query_does_not_rotate_endpoints():
    """A 400 is our bug - another mirror would reject it identically, so
    rotating would just spread a broken query around."""
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(400, text="parse error")

    http_client, client = client_with(handler, [A, B])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    assert elements == []
    assert client.endpoint_switches == 0
    assert set(seen) == {A}


@pytest.mark.asyncio
async def test_a_single_endpoint_still_works_and_cannot_rotate():
    def handler(request):
        return httpx.Response(429, text="busy")

    http_client, client = client_with(handler, [A])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    assert elements == []
    assert client.endpoint_switches == 0


@pytest.mark.asyncio
async def test_transport_errors_also_trigger_a_switch():
    def handler(request):
        if str(request.url) == A:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=OK_BODY)

    http_client, client = client_with(handler, [A, B])
    async with http_client:
        elements = await client.fetch_elements(BBOX, "amenity", "cafe")

    assert len(elements) == 1
    assert client.endpoint == B


@pytest.mark.asyncio
async def test_endpoint_list_falls_back_to_the_default_when_empty():
    def handler(request):
        return httpx.Response(200, json=OK_BODY)

    http_client, client = client_with(handler, [])
    async with http_client:
        assert "overpass-api.de" in client.endpoint
        await client.fetch_elements(BBOX, "amenity", "cafe")
