"""Unit tests for overpass_client.py - hand-built fixture JSON and a mocked
httpx transport, no real network, no real Overpass API calls."""

import httpx
import pytest

from workers.osm.overpass_client import OverpassClient, build_query, parse_elements

# --- build_query() / parse_elements(): pure, no network ----------------


def test_build_query_with_exact_value():
    query = build_query((55.5, 37.3, 55.9, 37.8), "amenity", "cafe", limit=50)

    assert 'node["amenity"="cafe"](55.5,37.3,55.9,37.8);' in query
    assert 'way["amenity"="cafe"](55.5,37.3,55.9,37.8);' in query
    assert 'relation["amenity"="cafe"](55.5,37.3,55.9,37.8);' in query
    assert "out tags 50;" in query


def test_build_query_with_any_value_key_only():
    query = build_query((55.5, 37.3, 55.9, 37.8), "shop", None, limit=50)

    assert 'node["shop"](55.5,37.3,55.9,37.8);' in query
    assert '["shop"="' not in query


def test_parses_elements_from_a_normal_response():
    payload = {
        "version": 0.6,
        "generator": "Overpass API",
        "elements": [
            {"type": "node", "id": 1, "tags": {"name": "Кафе Раз"}},
            {"type": "way", "id": 2, "tags": {"name": "Кафе Два"}},
        ],
    }

    elements = parse_elements(payload)

    assert len(elements) == 2
    assert elements[0]["tags"]["name"] == "Кафе Раз"


def test_missing_elements_key_does_not_raise():
    assert parse_elements({"version": 0.6}) == []


def test_completely_empty_payload_does_not_raise():
    assert parse_elements({}) == []


# --- OverpassClient: request shape + retry, via a mocked transport -----


@pytest.mark.asyncio
async def test_sends_user_agent_and_uses_query_language_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("User-Agent")
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"elements": []})

    # NOTE: no "@", "://" or "at ... dot ..." pattern here on purpose - the
    # real public Overpass instance 406s on those (see README.md), this
    # mocked transport wouldn't catch that, but the example shouldn't
    # perpetuate a format that fails against the real thing.
    test_user_agent = "test-agent/1.0 (contact via test suite)"

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OverpassClient(http_client, user_agent=test_user_agent, request_delay_seconds=0, page_size=10)
        await client.fetch_elements((55.5, 37.3, 55.9, 37.8), "amenity", "cafe")

    assert captured["user_agent"] == test_user_agent
    assert "data=" in captured["body"] and "amenity" in captured["body"]


@pytest.mark.asyncio
async def test_retries_past_429_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json={"elements": [{"type": "node", "id": 1, "tags": {"name": "X"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OverpassClient(http_client, user_agent="test-agent", request_delay_seconds=0, page_size=10)
        elements = await client.fetch_elements((55.5, 37.3, 55.9, 37.8), "amenity", "cafe")

    assert attempts["n"] == 3
    assert len(elements) == 1


@pytest.mark.asyncio
async def test_retries_past_504_gateway_timeout():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(504, text="Gateway Timeout")
        return httpx.Response(200, json={"elements": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OverpassClient(http_client, user_agent="test-agent", request_delay_seconds=0, page_size=10)
        elements = await client.fetch_elements((55.5, 37.3, 55.9, 37.8), "amenity", "cafe")

    assert attempts["n"] == 2
    assert elements == []


@pytest.mark.asyncio
async def test_does_not_retry_on_non_retryable_4xx():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, text="bad query")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OverpassClient(http_client, user_agent="test-agent", request_delay_seconds=0, page_size=10)
        elements = await client.fetch_elements((55.5, 37.3, 55.9, 37.8), "amenity", "cafe")

    assert attempts["n"] == 1
    assert elements == []
