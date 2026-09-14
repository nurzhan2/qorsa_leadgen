"""Tests for fetcher.py against a fake web (httpx.MockTransport): robots.txt
enforcement on every hop, pacing, the host guard, body limits. No real
network, no real waiting."""

import httpx
import pytest

from workers.enrich.fetcher import BlockedHost, RobotsDisallowed, SiteUnavailable, product_token
from workers.enrich.tests.support import (
    UA,
    FakeWeb,
    NoSleep,
    fail,
    html,
    make_fetcher,
    raw,
    redirect,
    static_guard,
    text,
)

ROBOTS = "https://romashka.kz/robots.txt"
HOME = "https://romashka.kz/"
CONTACTS = "https://romashka.kz/kontakty/"
ALLOW = "User-agent: *\nDisallow:\n"


def test_product_token_is_the_first_word_of_the_user_agent():
    assert product_token(UA) == "qorsa-leadgen-enrich"
    assert product_token("") == "*"


# --- robots.txt ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_robots_is_read_first_and_only_once_per_origin():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: html("<p>home</p>"), CONTACTS: html("<p>k</p>")})
    client, fetcher = make_fetcher(web)
    async with client:
        await fetcher.fetch_page(HOME)
        await fetcher.fetch_page(CONTACTS)

    assert web.urls == [ROBOTS, HOME, CONTACTS]


@pytest.mark.asyncio
async def test_a_disallowed_path_is_never_requested():
    web = FakeWeb({ROBOTS: text("User-agent: *\nDisallow: /kontakty/\n"), CONTACTS: html("<p>k</p>")})
    client, fetcher = make_fetcher(web)
    async with client:
        with pytest.raises(RobotsDisallowed) as info:
            await fetcher.fetch_page(CONTACTS)

    assert CONTACTS not in web.urls
    assert info.value.detail == "Disallow: /kontakty/"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_a_missing_robots_txt_allows_the_page(status):
    web = FakeWeb({ROBOTS: text("", status=status), HOME: html("<p>home</p>")})
    client, fetcher = make_fetcher(web)
    async with client:
        page = await fetcher.fetch_page(HOME)

    assert page.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_a_refused_or_broken_robots_txt_keeps_us_out(status):
    web = FakeWeb({ROBOTS: text("", status=status), HOME: html("<p>home</p>")})
    client, fetcher = make_fetcher(web)
    async with client:
        with pytest.raises(RobotsDisallowed) as info:
            await fetcher.fetch_page(HOME)

    assert HOME not in web.urls
    assert str(status) in info.value.detail


@pytest.mark.asyncio
async def test_robots_txt_that_cannot_be_reached_means_the_site_is_unavailable():
    web = FakeWeb({ROBOTS: fail(httpx.ConnectError), HOME: html("<p>home</p>")})
    client, fetcher = make_fetcher(web)
    async with client:
        with pytest.raises(SiteUnavailable):
            await fetcher.fetch_page(HOME)

    assert HOME not in web.urls


@pytest.mark.asyncio
async def test_a_redirected_robots_txt_is_followed():
    web = FakeWeb({
        ROBOTS: redirect("https://www.romashka.kz/robots.txt"),
        "https://www.romashka.kz/robots.txt": text("User-agent: *\nDisallow: /\n"),
        HOME: html("<p>home</p>"),
    })
    client, fetcher = make_fetcher(web)
    async with client:
        with pytest.raises(RobotsDisallowed):
            await fetcher.fetch_page(HOME)


@pytest.mark.asyncio
async def test_respect_robots_off_does_not_even_download_it():
    web = FakeWeb({ROBOTS: text("User-agent: *\nDisallow: /\n"), HOME: html("<p>home</p>")})
    client, fetcher = make_fetcher(web, respect_robots=False)
    async with client:
        await fetcher.fetch_page(HOME)

    assert web.urls == [HOME]


# --- identity and pacing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_request_carries_the_bot_user_agent():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: html("<p>home</p>")})
    client, fetcher = make_fetcher(web)
    async with client:
        await fetcher.fetch_page(HOME)

    assert web.requests
    assert all(r.headers["user-agent"] == UA for r in web.requests)


@pytest.mark.asyncio
async def test_requests_to_one_site_are_spaced_by_the_configured_delay():
    sleep = NoSleep()
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: html("<p>home</p>"), CONTACTS: html("<p>k</p>")})
    client, fetcher = make_fetcher(web, request_delay_seconds=2.0, sleep=sleep)
    async with client:
        await fetcher.fetch_page(HOME)
        await fetcher.fetch_page(CONTACTS)

    # robots.txt goes first with no wait, then 2 s before each page.
    assert sleep.calls == [2.0, 2.0]


@pytest.mark.asyncio
async def test_a_larger_crawl_delay_from_robots_txt_wins():
    sleep = NoSleep()
    web = FakeWeb({ROBOTS: text("User-agent: *\nCrawl-delay: 5\n"), HOME: html("<p>home</p>")})
    client, fetcher = make_fetcher(web, request_delay_seconds=2.0, sleep=sleep)
    async with client:
        await fetcher.fetch_page(HOME)

    assert sleep.calls == [5.0]


@pytest.mark.asyncio
async def test_an_absurd_crawl_delay_is_capped():
    sleep = NoSleep()
    web = FakeWeb({ROBOTS: text("User-agent: *\nCrawl-delay: 600\n"), HOME: html("<p>home</p>")})
    client, fetcher = make_fetcher(web, request_delay_seconds=2.0, max_crawl_delay_seconds=30.0, sleep=sleep)
    async with client:
        await fetcher.fetch_page(HOME)

    assert sleep.calls == [30.0]


# --- redirects: every hop is checked -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_redirect_to_another_site_checks_that_sites_robots_txt():
    other_robots = "https://www.romashka-group.kz/robots.txt"
    web = FakeWeb({
        ROBOTS: text(ALLOW),
        HOME: redirect("https://www.romashka-group.kz/"),
        other_robots: text("User-agent: *\nDisallow: /\n"),
        "https://www.romashka-group.kz/": html("<p>group</p>"),
    })
    client, fetcher = make_fetcher(web)
    async with client:
        with pytest.raises(RobotsDisallowed):
            await fetcher.fetch_page(HOME)

    assert web.urls == [ROBOTS, HOME, other_robots]


@pytest.mark.asyncio
async def test_a_redirect_into_the_local_network_is_refused_before_any_request():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: redirect("http://127.0.0.1:8081/api/v1/leads/top")})
    client, fetcher = make_fetcher(web, host_guard=static_guard)
    async with client:
        with pytest.raises(BlockedHost):
            await fetcher.fetch_page(HOME)

    assert not any("127.0.0.1" in url for url in web.urls)


@pytest.mark.asyncio
async def test_a_relative_redirect_is_resolved_and_the_final_url_reported():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: redirect("/ru/", 302), "https://romashka.kz/ru/": html("<p>ru</p>")})
    client, fetcher = make_fetcher(web)
    async with client:
        page = await fetcher.fetch_page(HOME)

    assert page.url == "https://romashka.kz/ru/"
    assert "ru" in page.html


@pytest.mark.asyncio
async def test_a_redirect_loop_gives_up():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: redirect(HOME)})
    client, fetcher = make_fetcher(web)
    async with client:
        with pytest.raises(SiteUnavailable):
            await fetcher.fetch_page(HOME)

    assert web.urls.count(HOME) == 6  # the first request + 5 redirects


# --- bodies and failures ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_huge_page_is_truncated():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: html("<p>" + "x" * 50_000 + "</p>")})
    client, fetcher = make_fetcher(web, max_page_bytes=10_000)
    async with client:
        page = await fetcher.fetch_page(HOME)

    assert len(page.html) <= 10_000


@pytest.mark.asyncio
async def test_a_non_html_response_is_not_parsed():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: raw(200, "application/pdf", b"%PDF-1.7 ...")})
    client, fetcher = make_fetcher(web)
    async with client:
        page = await fetcher.fetch_page(HOME)

    assert page.html is None
    assert page.content_type == "application/pdf"


@pytest.mark.asyncio
async def test_an_error_status_is_returned_not_raised():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: html("<p>down</p>", status=503)})
    client, fetcher = make_fetcher(web)
    async with client:
        page = await fetcher.fetch_page(HOME)

    assert page.status == 503
    assert page.html is None


@pytest.mark.asyncio
async def test_a_timeout_is_site_unavailable():
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: fail(httpx.ReadTimeout, "read timed out")})
    client, fetcher = make_fetcher(web)
    async with client:
        with pytest.raises(SiteUnavailable, match="таймаут"):
            await fetcher.fetch_page(HOME)


@pytest.mark.asyncio
async def test_a_windows_1251_page_is_decoded():
    body = "<html><body><a href='/kontakty/'>Контакты</a></body></html>"
    web = FakeWeb({ROBOTS: text(ALLOW), HOME: html(body, charset="windows-1251")})
    client, fetcher = make_fetcher(web)
    async with client:
        page = await fetcher.fetch_page(HOME)

    assert "Контакты" in page.html
