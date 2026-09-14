"""End-to-end tests for SiteEnricher: a fake website (httpx.MockTransport)
built from the fixtures, a real PoliteFetcher, a real extractor. Covers the
full per-company flow - robots.txt, homepage, contact pages, fallbacks, and
every way a site can fail to cooperate."""

import httpx
import pytest

from workers.enrich.core_client import PendingCompany
from workers.enrich.enricher import FOUND, NOTHING_FOUND, ROBOTS_BLOCKED, SKIPPED, UNAVAILABLE, SiteEnricher
from workers.enrich.tests.support import (
    UA,
    FakeWeb,
    fail,
    html,
    load_fixture,
    make_fetcher,
    raw,
    redirect,
    static_guard,
    text,
)

ROBOTS_OK = "User-agent: *\nDisallow: /bitrix/\nDisallow: /search/\n"
EMPTY_PAGE = "<html><body><p>Здесь ничего полезного.</p></body></html>"


def romashka(scheme: str = "https", **overrides) -> FakeWeb:
    base = f"{scheme}://romashka.kz"
    routes = {
        f"{base}/robots.txt": text(ROBOTS_OK),
        f"{base}/": html(load_fixture("home_page.html")),
        f"{base}/kontakty/": html(load_fixture("contacts_page.html")),
        f"{base}/feedback/": html(EMPTY_PAGE),
        f"{base}/o-kompanii/": html(EMPTY_PAGE),
    }
    routes.update(overrides)
    return FakeWeb(routes)


def company(**overrides) -> PendingCompany:
    base = dict(id="c-1", name="ТОО Ромашка", domain="romashka.kz", city="Алматы")
    base.update(overrides)
    return PendingCompany(**base)


async def enrich(web: FakeWeb, target: PendingCompany | None = None, **fetcher_options):
    client, fetcher = make_fetcher(web, **fetcher_options)
    async with client:
        return await SiteEnricher(fetcher, max_contact_pages=3).enrich(target or company())


# --- the happy path ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finds_the_contacts_and_says_where_they_came_from():
    outcome = await enrich(romashka())

    assert outcome.status == FOUND
    assert outcome.email == "info@romashka.kz"
    assert outcome.phone == "+77273551020; +78005553535; +77012345678"
    assert outcome.messenger == "https://wa.me/77012345678, https://t.me/romashka_kz"
    assert outcome.notes == "email с /kontakty/; телефон с главной; мессенджер с /kontakty/"
    assert outcome.foreign_email is False


@pytest.mark.asyncio
async def test_robots_txt_is_the_very_first_request():
    web = romashka()
    await enrich(web)

    assert web.urls[0] == "https://romashka.kz/robots.txt"


@pytest.mark.asyncio
async def test_stops_visiting_pages_once_it_has_what_the_company_lacks():
    web = romashka()
    await enrich(web)

    # Phone was on the homepage, a corporate email on /kontakty/ - nothing
    # left to look for, so the other two candidate pages are never requested.
    assert web.urls == ["https://romashka.kz/robots.txt", "https://romashka.kz/",
                        "https://romashka.kz/kontakty/"]


@pytest.mark.asyncio
async def test_only_what_the_company_is_missing_is_reported():
    web = romashka()
    outcome = await enrich(web, company(email="boss@romashka.kz", messenger="https://t.me/boss"))

    assert outcome.email is None
    assert outcome.messenger is None
    assert outcome.phone == "+77273551020"
    assert "email" not in outcome.notes
    # Only a phone was missing and the homepage had one: no contact page needed.
    assert web.urls == ["https://romashka.kz/robots.txt", "https://romashka.kz/"]


@pytest.mark.asyncio
async def test_every_request_identifies_the_bot():
    web = romashka()
    await enrich(web)

    assert all(r.headers["user-agent"] == UA for r in web.requests)


# --- robots.txt ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_contacts_page_closed_by_robots_txt_is_never_requested():
    web = romashka(**{"https://romashka.kz/robots.txt": text("User-agent: *\nDisallow: /kontakty/\n")})
    outcome = await enrich(web)

    assert "https://romashka.kz/kontakty/" not in web.urls
    assert "https://romashka.kz/feedback/" in web.urls  # the next candidate is still fine
    assert outcome.email is None  # it was only on the page we may not read
    assert outcome.phone == "+77273551020"
    assert "robots.txt закрыл страниц: 1" in outcome.notes


@pytest.mark.asyncio
async def test_disallow_all_means_not_even_the_homepage():
    web = romashka(**{"https://romashka.kz/robots.txt": text("User-agent: *\nDisallow: /\n")})
    outcome = await enrich(web)

    assert outcome.status == ROBOTS_BLOCKED
    assert web.urls == ["https://romashka.kz/robots.txt"]
    assert "robots.txt запрещает" in outcome.notes


@pytest.mark.asyncio
async def test_a_group_addressed_to_our_bot_is_obeyed():
    robots = "User-agent: *\nAllow: /\n\nUser-agent: qorsa-leadgen-enrich\nDisallow: /\n"
    web = romashka(**{"https://romashka.kz/robots.txt": text(robots)})
    outcome = await enrich(web)

    assert outcome.status == ROBOTS_BLOCKED
    assert "https://romashka.kz/" not in web.urls


@pytest.mark.asyncio
async def test_rules_for_other_bots_do_not_stop_us():
    web = romashka(**{"https://romashka.kz/robots.txt": text("User-agent: Googlebot\nDisallow: /\n")})
    assert (await enrich(web)).status == FOUND


@pytest.mark.asyncio
async def test_a_broken_robots_txt_keeps_us_out():
    web = romashka(**{"https://romashka.kz/robots.txt": text("oops", status=503)})
    outcome = await enrich(web)

    assert outcome.status == ROBOTS_BLOCKED
    assert "503" in outcome.notes
    assert "https://romashka.kz/" not in web.urls


# --- sites that don't cooperate ------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unreachable_site_is_an_outcome_not_an_exception():
    web = FakeWeb({
        "https://romashka.kz/robots.txt": fail(httpx.ConnectError, "Name or service not known"),
        "http://romashka.kz/robots.txt": fail(httpx.ConnectError, "Name or service not known"),
    })
    outcome = await enrich(web)

    assert outcome.status == UNAVAILABLE
    assert outcome.notes.startswith("сайт недоступен")
    assert not outcome.found_anything
    # https first, then the plain-http fallback, then it gives up.
    assert web.urls == ["https://romashka.kz/robots.txt", "http://romashka.kz/robots.txt"]


@pytest.mark.asyncio
async def test_a_site_that_times_out_is_unavailable():
    web = FakeWeb({
        "https://romashka.kz/robots.txt": text(ROBOTS_OK),
        "https://romashka.kz/": fail(httpx.ReadTimeout, "timed out"),
        "http://romashka.kz/robots.txt": text(ROBOTS_OK),
        "http://romashka.kz/": fail(httpx.ReadTimeout, "timed out"),
    })
    outcome = await enrich(web)

    assert outcome.status == UNAVAILABLE
    assert "таймаут" in outcome.notes


@pytest.mark.asyncio
async def test_falls_back_to_plain_http_when_https_is_broken():
    web = romashka("http", **{"https://romashka.kz/robots.txt": fail(httpx.ConnectError, "SSL handshake failed")})
    outcome = await enrich(web)

    assert outcome.status == FOUND
    assert outcome.email == "info@romashka.kz"
    assert "http://romashka.kz/robots.txt" in web.urls  # http:// is its own origin


@pytest.mark.asyncio
async def test_a_homepage_error_status_is_unavailable():
    web = romashka(**{"https://romashka.kz/": html("<h1>Service Unavailable</h1>", status=503)})
    outcome = await enrich(web)

    assert outcome.status == UNAVAILABLE
    assert outcome.notes == "главная ответила HTTP 503"


@pytest.mark.asyncio
async def test_a_non_html_homepage_finds_nothing():
    web = romashka(**{"https://romashka.kz/": raw(200, "application/pdf", b"%PDF-1.7")})
    outcome = await enrich(web)

    assert outcome.status == NOTHING_FOUND
    assert "не HTML" in outcome.notes


@pytest.mark.asyncio
async def test_a_site_without_contacts_is_a_normal_nothing_found():
    web = romashka(**{"https://romashka.kz/": html(EMPTY_PAGE)})
    outcome = await enrich(web)

    assert outcome.status == NOTHING_FOUND
    assert outcome.notes == "контакты не найдены (страниц просмотрено: 1)"
    assert outcome.to_patch().model_dump(by_alias=True, exclude_none=True) == {
        "enrichNotes": "контакты не найдены (страниц просмотрено: 1)"}


@pytest.mark.asyncio
async def test_a_broken_contacts_page_does_not_lose_what_the_homepage_had():
    web = romashka(**{"https://romashka.kz/kontakty/": fail(httpx.ReadTimeout, "timed out")})
    outcome = await enrich(web)

    assert outcome.status == FOUND
    assert outcome.phone == "+77273551020"


# --- which email, and the notes about it ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_free_mail_address_is_taken_but_flagged():
    contacts = "<html><body><h1>Контакты</h1><p>Пишите: romashka.almaty@gmail.com</p></body></html>"
    web = romashka(**{"https://romashka.kz/kontakty/": html(contacts)})
    outcome = await enrich(web)

    assert outcome.email == "romashka.almaty@gmail.com"
    assert outcome.foreign_email is True
    assert "email на чужом домене gmail.com" in outcome.notes


@pytest.mark.asyncio
async def test_after_a_redirect_the_new_domain_counts_as_the_companys_own():
    group = "https://www.romashka-group.kz"
    web = FakeWeb({
        "https://romashka.kz/robots.txt": text(ROBOTS_OK),
        "https://romashka.kz/": redirect(f"{group}/"),
        f"{group}/robots.txt": text(ROBOTS_OK),
        f"{group}/": html('<a href="/contacts/">Контакты</a>'),
        f"{group}/contacts/": html("<p>sales@romashka-group.kz, romashka@gmail.com</p>"),
    })
    outcome = await enrich(web)

    assert outcome.email == "sales@romashka-group.kz"
    assert outcome.foreign_email is False
    assert f"{group}/robots.txt" in web.urls


# --- domains we refuse to visit -----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["vk.com", "instagram.com", "taplink.cc", "m.vk.com"])
async def test_a_social_or_platform_domain_is_skipped_without_a_request(domain):
    web = romashka()
    outcome = await enrich(web, company(domain=domain))

    assert outcome.status == SKIPPED
    assert "площадка" in outcome.notes
    assert web.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["не домен", "localhost", "192.168.1.10", ""])
async def test_a_garbage_domain_is_skipped_without_a_request(domain):
    web = romashka()
    outcome = await enrich(web, company(domain=domain))

    assert outcome.status == SKIPPED
    assert web.requests == []


@pytest.mark.asyncio
async def test_a_redirect_into_the_local_network_is_refused():
    web = romashka(**{"https://romashka.kz/": redirect("http://127.0.0.1:8081/api/v1/leads/top", 302)})
    outcome = await enrich(web, host_guard=static_guard)

    assert outcome.status == SKIPPED
    assert "не обходим" in outcome.notes
    assert not any("127.0.0.1" in url for url in web.urls)


@pytest.mark.asyncio
async def test_a_domain_with_scheme_and_path_is_cleaned_first():
    web = romashka()
    outcome = await enrich(web, company(domain="HTTPS://Romashka.KZ/about?utm=hh"))

    assert outcome.status == FOUND
    assert web.urls[0] == "https://romashka.kz/robots.txt"
