"""Tests for core_client.py via a mocked httpx transport - no real core."""

import json

import httpx
import pytest

from workers.enrich.core_client import ContactsPatch, CoreClient, CoreUnavailable

FAST = {"retry_attempts": 3, "retry_base_seconds": 0.001, "retry_max_seconds": 0.002}


def make(handler):
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return http_client, CoreClient("http://core:8081/", client=http_client, **FAST)


@pytest.mark.asyncio
async def test_fetch_pending_sends_the_limit_and_parses_companies():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[{
            "id": "7f3c3c2e-0000-4000-8000-000000000001", "name": "Ромашка", "domain": "romashka.kz",
            "email": None, "phone": "+77273551020", "messenger": None, "city": "Алматы",
            "somethingNew": "ignored",
        }])

    http_client, core = make(handler)
    async with http_client:
        pending = await core.fetch_pending(50)

    assert seen == {"path": "/api/v1/companies/pending-enrich", "params": {"limit": "50"}}
    assert pending[0].domain == "romashka.kz"
    assert pending[0].email is None
    assert pending[0].phone == "+77273551020"


@pytest.mark.asyncio
async def test_patch_sends_camel_case_and_leaves_out_empty_fields():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"filled": ["email"]})

    http_client, core = make(handler)
    async with http_client:
        ok = await core.patch_contacts("c-1", ContactsPatch(email="info@romashka.kz",
                                                            enrich_notes="email с /kontakty/"))

    assert ok is True
    assert seen == {"method": "PATCH", "path": "/api/v1/companies/c-1/contacts",
                    "body": {"email": "info@romashka.kz", "enrichNotes": "email с /kontakty/"}}


@pytest.mark.asyncio
async def test_an_empty_patch_is_still_sent():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    http_client, core = make(handler)
    async with http_client:
        assert await core.patch_contacts("c-1", ContactsPatch()) is True

    assert seen["body"] == {}


@pytest.mark.asyncio
async def test_5xx_is_retried():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(503) if attempts["n"] < 3 else httpx.Response(200, json={})

    http_client, core = make(handler)
    async with http_client:
        assert await core.patch_contacts("c-1", ContactsPatch()) is True

    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_404_is_not_retried():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(404, json={"error": "not found"})

    http_client, core = make(handler)
    async with http_client:
        assert await core.patch_contacts("gone", ContactsPatch()) is False

    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_a_core_that_is_down_raises_core_unavailable_after_retries():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    http_client, core = make(handler)
    async with http_client:
        with pytest.raises(CoreUnavailable):
            await core.fetch_pending(50)

    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_a_patch_to_a_core_that_is_down_returns_false_instead_of_raising():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    http_client, core = make(handler)
    async with http_client:
        assert await core.patch_contacts("c-1", ContactsPatch()) is False
