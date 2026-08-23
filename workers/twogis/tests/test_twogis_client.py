"""Unit tests for twogis_client.py - hand-built fixture JSON and a mocked
httpx transport, no real network, no real 2GIS API calls."""

import httpx
import pytest

from workers.twogis.twogis_client import TwoGisClient, parse_items

# --- parse_items(): pure parsing, no network ---------------------------


def test_parses_items_and_total_from_a_normal_response():
    payload = {
        "meta": {"code": 200},
        "result": {
            "items": [
                {"id": "1", "full_name": "Кофейня Ромашка"},
                {"id": "2", "full_name": "Sunrise Auto"},
            ],
            "total": 42,
        },
    }

    items, total = parse_items(payload)

    assert len(items) == 2
    assert items[0]["full_name"] == "Кофейня Ромашка"
    assert total == 42


def test_empty_items_list_yields_no_items():
    payload = {"result": {"items": [], "total": 0}}

    items, total = parse_items(payload)

    assert items == []
    assert total == 0


def test_missing_result_key_does_not_raise():
    payload = {"meta": {"code": 200}}

    items, total = parse_items(payload)

    assert items == []
    assert total == 0


def test_missing_items_key_does_not_raise():
    payload = {"result": {"total": 5}}

    items, total = parse_items(payload)

    assert items == []
    assert total == 5


def test_missing_total_falls_back_to_item_count():
    payload = {"result": {"items": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}}

    items, total = parse_items(payload)

    assert len(items) == 3
    assert total == 3


def test_completely_empty_payload_does_not_raise():
    items, total = parse_items({})

    assert items == []
    assert total == 0


# --- TwoGisClient: request shape + pagination, via a mocked transport --


@pytest.mark.asyncio
async def test_request_uses_region_id_and_rubric_only_text_query():
    """The whole point of this fix: q must be JUST the rubric text, and
    region_id must be a real request param - not the city folded into q."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.url.params.get("q")
        captured["region_id"] = request.url.params.get("region_id")
        return httpx.Response(200, json={"result": {"items": [], "total": 0}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = TwoGisClient("fake-key", http_client, request_delay_seconds=0, page_size=20)
        _ = [item async for item in client.iter_items(region_id=38, rubric_query="кофейня")]

    assert captured["q"] == "кофейня"
    assert captured["region_id"] == "38"
    assert "Санкт-Петербург" not in (captured["q"] or "")


@pytest.mark.asyncio
async def test_pagination_stops_on_short_page():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page"))
        items = [{"id": str(i)} for i in range(5)] if page == 1 else [{"id": "99"}]
        return httpx.Response(200, json={"result": {"items": items, "total": 6}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = TwoGisClient("fake-key", http_client, request_delay_seconds=0, page_size=5, max_pages_per_combo=5)
        items = [item async for item in client.iter_items(region_id=38, rubric_query="кафе")]

    assert len(items) == 6


@pytest.mark.asyncio
async def test_pagination_stops_when_total_is_reached_even_on_a_full_page():
    """If total is an exact multiple of page_size, the last page is still
    "full" - pagination must stop based on total, not just page length."""
    pages_seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page"))
        pages_seen.append(page)
        items = [{"id": str(i)} for i in range(5)]
        return httpx.Response(200, json={"result": {"items": items, "total": 10}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = TwoGisClient("fake-key", http_client, request_delay_seconds=0, page_size=5, max_pages_per_combo=10)
        items = [item async for item in client.iter_items(region_id=1, rubric_query="ресторан")]

    assert pages_seen == [1, 2]
    assert len(items) == 10


@pytest.mark.asyncio
async def test_pagination_capped_by_max_pages_per_combo_even_if_more_total_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        items = [{"id": str(request.url.params.get("page")) + "-x"}]
        return httpx.Response(200, json={"result": {"items": items, "total": 3129}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = TwoGisClient("fake-key", http_client, request_delay_seconds=0, page_size=1, max_pages_per_combo=3)
        items = [item async for item in client.iter_items(region_id=38, rubric_query="кофейня")]

    assert len(items) == 3  # capped, not 3129


@pytest.mark.asyncio
async def test_retries_past_429_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json={"result": {"items": [{"id": "1"}], "total": 1}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = TwoGisClient("fake-key", http_client, request_delay_seconds=0, page_size=20)
        items = [item async for item in client.iter_items(region_id=38, rubric_query="кафе")]

    assert attempts["n"] == 3
    assert len(items) == 1


@pytest.mark.asyncio
async def test_does_not_retry_on_non_retryable_4xx():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, text="bad request")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = TwoGisClient("fake-key", http_client, request_delay_seconds=0, page_size=20, max_pages_per_combo=3)
        items = [item async for item in client.iter_items(region_id=38, rubric_query="бот")]

    assert attempts["n"] == 1
    assert items == []
