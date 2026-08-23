"""Unit tests for places_client.py - hand-built fixture JSON and a mocked
httpx transport, no real network, no real Google API calls."""

import httpx
import pytest

from workers.google_places.places_client import GooglePlacesClient, parse_places

# --- parse_places(): pure parsing, no network ---------------------------


def test_parses_places_and_next_page_token():
    payload = {
        "places": [
            {"id": "1", "displayName": {"text": "Кофейня Ромашка"}},
            {"id": "2", "displayName": {"text": "Sunrise Auto"}},
        ],
        "nextPageToken": "abc123",
    }

    places, token = parse_places(payload)

    assert len(places) == 2
    assert places[0]["displayName"]["text"] == "Кофейня Ромашка"
    assert token == "abc123"


def test_missing_next_page_token_is_none():
    places, token = parse_places({"places": [{"id": "1"}]})

    assert token is None


def test_missing_places_key_does_not_raise():
    places, token = parse_places({})

    assert places == []
    assert token is None


def test_empty_places_list():
    places, token = parse_places({"places": []})

    assert places == []


# --- GooglePlacesClient: request shape + pagination + retry ------------


@pytest.mark.asyncio
async def test_sends_api_key_and_field_mask_headers():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.headers.get("X-Goog-Api-Key")
        captured["field_mask"] = request.headers.get("X-Goog-FieldMask")
        import json

        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"places": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GooglePlacesClient("fake-key", http_client, request_delay_seconds=0, page_token_delay_seconds=0)
        _ = [p async for p in client.iter_places("кафе Москва")]

    assert captured["api_key"] == "fake-key"
    assert "places.websiteUri" in captured["field_mask"]
    assert "places.nationalPhoneNumber" in captured["field_mask"]
    assert captured["body"] == {"textQuery": "кафе Москва"}


@pytest.mark.asyncio
async def test_pagination_follows_next_page_token():
    requests_seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.read())
        requests_seen.append(body.get("pageToken"))
        if body.get("pageToken") is None:
            return httpx.Response(200, json={"places": [{"id": "1"}], "nextPageToken": "page2"})
        return httpx.Response(200, json={"places": [{"id": "2"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GooglePlacesClient("fake-key", http_client, request_delay_seconds=0, page_token_delay_seconds=0,
                                     max_pages_per_query=5)
        places = [p async for p in client.iter_places("кафе Москва")]

    assert requests_seen == [None, "page2"]
    assert [p["id"] for p in places] == ["1", "2"]


@pytest.mark.asyncio
async def test_pagination_capped_by_max_pages_per_query():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"places": [{"id": str(call_count["n"])}], "nextPageToken": "more"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GooglePlacesClient("fake-key", http_client, request_delay_seconds=0, page_token_delay_seconds=0,
                                     max_pages_per_query=2)
        places = [p async for p in client.iter_places("кафе Москва")]

    assert call_count["n"] == 2
    assert len(places) == 2


@pytest.mark.asyncio
async def test_retries_past_429_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, text="RESOURCE_EXHAUSTED")
        return httpx.Response(200, json={"places": [{"id": "1"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GooglePlacesClient("fake-key", http_client, request_delay_seconds=0, page_token_delay_seconds=0)
        places = [p async for p in client.iter_places("кафе Москва")]

    assert attempts["n"] == 3
    assert len(places) == 1


@pytest.mark.asyncio
async def test_does_not_retry_on_billing_disabled_error():
    """A realistic Places API (New) billing error - 400 with a clear
    message, no retry storm."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(
            403,
            json={"error": {"code": 403, "message": "This API method requires billing to be enabled.",
                             "status": "PERMISSION_DENIED"}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GooglePlacesClient("fake-key", http_client, request_delay_seconds=0, page_token_delay_seconds=0)
        places = [p async for p in client.iter_places("кафе Москва")]

    assert attempts["n"] == 1
    assert places == []
