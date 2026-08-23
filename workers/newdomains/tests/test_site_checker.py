"""Unit tests for site_checker.py - pure logic tests plus a mocked httpx
transport for check_has_site(). No real network requests."""

import httpx
import pytest

from workers.newdomains.site_checker import check_has_site, looks_parked_or_empty

REAL_SITE_BODY = """
<html><head><title>Кофейня Ромашка - уютное место в центре города</title></head>
<body>
<header><nav>Главная | Меню | О нас | Контакты</nav></header>
<main>
<h1>Добро пожаловать в Кофейню Ромашка</h1>
<p>Мы варим лучший кофе в городе с 2015 года. Загляните к нам на чашечку
эспрессо или капучино - у нас уютная атмосфера, свежая выпечка каждый день
и приветливый персонал, который всегда рад новым и постоянным гостям.</p>
<section><h2>Наше меню</h2><ul><li>Эспрессо</li><li>Капучино</li><li>Латте</li></ul></section>
</main>
</body></html>
"""

PARKING_BODY = """
<html><body>
<h1>This domain may be for sale!</h1>
<p>Buy this domain now. Contact us for pricing.</p>
</body></html>
"""

EMPTY_BODY = "<html><body></body></html>"


# --- looks_parked_or_empty(): pure ---------------------------------------


def test_real_content_is_not_parked():
    assert looks_parked_or_empty(200, REAL_SITE_BODY) is False


def test_parking_page_text_is_detected():
    assert looks_parked_or_empty(200, PARKING_BODY) is True


def test_short_empty_body_is_treated_as_no_site():
    assert looks_parked_or_empty(200, EMPTY_BODY) is True
    assert looks_parked_or_empty(200, "") is True
    assert looks_parked_or_empty(200, None) is True


def test_error_status_code_is_treated_as_no_site():
    assert looks_parked_or_empty(404, REAL_SITE_BODY) is True
    assert looks_parked_or_empty(500, REAL_SITE_BODY) is True


def test_various_parking_markers_are_case_insensitive():
    assert looks_parked_or_empty(200, "SEDO PARKING - " + "x" * 200) is True
    assert looks_parked_or_empty(200, "Coming Soon! " + "x" * 200) is True


# --- check_has_site(): mocked transport, async ---------------------------


@pytest.mark.asyncio
async def test_returns_true_for_a_real_site():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=REAL_SITE_BODY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await check_has_site("romashka-coffee.example", client) is True


@pytest.mark.asyncio
async def test_returns_false_for_a_parking_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PARKING_BODY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await check_has_site("freshly-bought.example", client) is False


@pytest.mark.asyncio
async def test_returns_false_when_both_schemes_fail_to_connect():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await check_has_site("nothing-here.example", client) is False


@pytest.mark.asyncio
async def test_falls_back_to_http_when_https_fails():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.scheme)
        if request.url.scheme == "https":
            raise httpx.ConnectError("no tls", request=request)
        return httpx.Response(200, text=REAL_SITE_BODY)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await check_has_site("http-only.example", client)

    assert result is True
    assert calls == ["https", "http"]
