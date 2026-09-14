"""One company -> one EnrichOutcome: visit the site, read the homepage and up
to N contact-ish pages, and decide what to send back to the core.

The outcome is ALWAYS sent (runner.py), whatever happened - "nothing found",
"site down" and "robots.txt says no" are normal results, not errors, and
the core must record the attempt so the company isn't handed out again.
"""

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import structlog

from .core_client import ContactsPatch, PendingCompany
from .domains import base_domain, clean_domain, platform_of
from .extractor import (
    EmailCandidate,
    Messenger,
    PageContacts,
    PhoneCandidate,
    extract_page,
    pick_messengers,
    rank_emails,
)
from .fetcher import BlockedHost, FetchError, Page, PoliteFetcher, RobotsDisallowed, SiteUnavailable

log = structlog.get_logger(__name__)

FOUND = "found"
NOTHING_FOUND = "nothing_found"
UNAVAILABLE = "unavailable"
ROBOTS_BLOCKED = "robots_blocked"
SKIPPED = "skipped"
FAILED = "failed"

#: raw.enrich_notes is capped at 1000 characters by the core.
MAX_NOTES_LENGTH = 1000
#: The core's messenger column is VARCHAR(255).
MAX_MESSENGER_LENGTH = 255


@dataclass
class EnrichOutcome:
    status: str
    email: str | None = None
    phone: str | None = None        # "+7...; +7..." - the core's PhoneUtil picks the one to call
    messenger: str | None = None
    notes: str = ""
    pages_fetched: int = 0
    foreign_email: bool = False

    @property
    def found_anything(self) -> bool:
        return bool(self.email or self.phone or self.messenger)

    def to_patch(self) -> ContactsPatch:
        return ContactsPatch(
            email=self.email,
            phone=self.phone,
            messenger=self.messenger,
            enrich_notes=self.notes[:MAX_NOTES_LENGTH] or None,
        )


@dataclass(frozen=True)
class _Needs:
    email: bool
    phone: bool
    messenger: bool


class _Collected:
    """Contacts gathered across the pages of one site, in discovery order."""

    def __init__(self):
        self.emails: list[EmailCandidate] = []
        self.phones: list[PhoneCandidate] = []
        self.messengers: list[Messenger] = []

    def add(self, page: PageContacts) -> None:
        self.emails.extend(page.emails)  # rank_emails() dedups
        known_phones = {p.phone for p in self.phones}
        self.phones.extend(p for p in page.phones if p.phone not in known_phones)
        known_messengers = {m.url for m in self.messengers}
        self.messengers.extend(m for m in page.messengers if m.url not in known_messengers)

    def has_own_email(self, own_bases: set[str]) -> bool:
        return any(base_domain(c.email.rsplit("@", 1)[1]) in own_bases for c in self.emails)


class SiteEnricher:
    def __init__(self, fetcher: PoliteFetcher, max_contact_pages: int = 3, max_phones: int = 3):
        self._fetcher = fetcher
        self._max_contact_pages = max_contact_pages
        self._max_phones = max_phones

    async def enrich(self, company: PendingCompany) -> EnrichOutcome:
        host = clean_domain(company.domain)
        if host is None:
            return EnrichOutcome(SKIPPED, notes=f"некорректный домен: {company.domain!r}"[:200])
        platform = platform_of(host)
        if platform is not None:
            return EnrichOutcome(SKIPPED, notes=f"{host} — площадка ({platform}), а не сайт компании")

        # Only look for what the company is missing: the core would ignore
        # the rest anyway, and the notes should describe what was applied.
        needs = _Needs(email=not _has(company.email), phone=not _has(company.phone),
                       messenger=not _has(company.messenger))

        try:
            home = await self._fetch_home(host)
        except RobotsDisallowed as exc:
            return EnrichOutcome(ROBOTS_BLOCKED, notes=f"robots.txt запрещает обход главной ({exc.detail})")
        except BlockedHost as exc:
            return EnrichOutcome(SKIPPED, notes=f"домен не обходим: {exc}")
        except SiteUnavailable as exc:
            return EnrichOutcome(UNAVAILABLE, notes=f"сайт недоступен: {exc}")

        if home.status >= 400:
            return EnrichOutcome(UNAVAILABLE, notes=f"главная ответила HTTP {home.status}", pages_fetched=1)
        if home.html is None:
            return EnrichOutcome(NOTHING_FOUND, pages_fetched=1,
                                 notes=f"главная — не HTML ({home.content_type or 'тип не указан'})")

        site_host = urlsplit(home.url).hostname or host
        own_bases = {base_domain(host), base_domain(site_host)}
        collected = _Collected()
        first = extract_page(home.html, home.url, max_links=self._max_contact_pages)
        collected.add(first)
        pages = 1
        robots_closed = 0

        for link in first.contact_links:
            if _satisfied(collected, needs, own_bases):
                break  # every missing field already has a good candidate - save the site a request
            try:
                page = await self._fetcher.fetch_page(link)
            except RobotsDisallowed:
                robots_closed += 1
                continue
            except FetchError as exc:
                log.debug("enrich.contact_page_failed", url=link, error=str(exc))
                continue
            pages += 1
            if page.status < 400 and page.html is not None:
                collected.add(extract_page(page.html, page.url, max_links=0))

        return self._outcome(collected, needs, own_bases, site_host, pages, robots_closed)

    async def _fetch_home(self, host: str) -> Page:
        try:
            return await self._fetcher.fetch_page(f"https://{host}/")
        except SiteUnavailable as https_error:
            # Plenty of small RU/KZ sites still have no (working) TLS. http://
            # is a different origin, so it gets its own robots.txt check.
            try:
                return await self._fetcher.fetch_page(f"http://{host}/")
            except SiteUnavailable as http_error:
                raise SiteUnavailable(f"https — {https_error}; http — {http_error}") from http_error

    def _outcome(self, collected: _Collected, needs: _Needs, own_bases: set[str], site_host: str,
                 pages: int, robots_closed: int) -> EnrichOutcome:
        ranked = rank_emails(collected.emails, own_bases) if needs.email else []
        best = ranked[0] if ranked else None
        phones = collected.phones[: self._max_phones] if needs.phone else []
        messengers = pick_messengers(collected.messengers) if needs.messenger else []

        notes: list[str] = []
        if best is not None:
            notes.append(f"email с {_where(best.page_url, site_host)}")
            if best.is_foreign:
                notes.append(f"email на чужом домене {best.domain}")
        if phones:
            notes.append(f"телефон с {_where(phones[0].page_url, site_host)}")
        if messengers:
            notes.append(f"мессенджер с {_where(messengers[0].page_url, site_host)}")
        if robots_closed:
            notes.append(f"robots.txt закрыл страниц: {robots_closed}")

        found = bool(best or phones or messengers)
        if not found:
            notes.insert(0, f"контакты не найдены (страниц просмотрено: {pages})")

        return EnrichOutcome(
            status=FOUND if found else NOTHING_FOUND,
            email=best.email if best else None,
            phone="; ".join(p.phone for p in phones) or None,
            messenger=", ".join(m.url for m in messengers)[:MAX_MESSENGER_LENGTH] or None,
            notes="; ".join(notes),
            pages_fetched=pages,
            foreign_email=bool(best and best.is_foreign),
        )


def _satisfied(collected: _Collected, needs: _Needs, own_bases: set[str]) -> bool:
    email_ok = not needs.email or collected.has_own_email(own_bases)
    phone_ok = not needs.phone or bool(collected.phones)
    return email_ok and phone_ok


def _where(url: str, site_host: str) -> str:
    """Short page label for notes: "главной", "/kontakty/", or
    "other-host.kz/contacts" when the page lives on another host."""
    parts = urlsplit(url)
    path = unquote(parts.path or "/")
    host = parts.hostname or ""
    if base_domain(host) != base_domain(site_host):
        shown = host + path
    elif path in ("", "/"):
        return "главной"
    else:
        shown = path
    return shown if len(shown) <= 60 else shown[:57] + "..."


def _has(value: str | None) -> bool:
    return bool(value and value.strip())
