"""Local site probe: what can be measured without any API key.

Deliberately small and boring. Every check answers a question a client would
recognise - "does it open", "does it work on a phone", "is it slow", "is it
heavy" - because the failed checks end up in the Sheets "Причина" column and
have to survive being read out loud on a cold call.

Each check returns a short stable label on failure and None on success. The
count of labels is what the core stores as auditFails.
"""

import ssl
import time

import httpx
import structlog
from bs4 import BeautifulSoup

log = structlog.get_logger(__name__)

# Labels are stable identifiers, not prose: they are stored in raw and shown
# in the sheet, so renaming one silently changes historical rows.
NO_HTTPS = "нет https"
BAD_CERT = "битый сертификат"
NO_HTTPS_REDIRECT = "http не редиректит на https"
UNREACHABLE = "сайт не открывается"
SLOW = "медленный отклик"
HEAVY = "тяжёлая страница"
NO_VIEWPORT = "нет мобильной вёрстки"
NO_TITLE = "нет title"
NO_DESCRIPTION = "нет meta description"
NO_H1 = "нет h1"
MIXED_CONTENT = "смешанный контент"
EMPTY_PAGE = "пустая страница"
NO_FAVICON = "нет favicon"
NO_OG_TAGS = "нет Open Graph (голая ссылка в соцсетях)"
IMAGES_WITHOUT_ALT = "картинки без alt"
TABLE_LAYOUT = "табличная вёрстка"


class ProbeResult:
    """Outcome of one site probe.

    `reachable=False` means nothing else could be measured - the failed-check
    list then carries a single reason, and the caller decides whether that is
    worth reporting as a score (it isn't: an unreachable site is a dead
    domain, not a bad site).
    """

    def __init__(self, reachable: bool, failed: list[str], final_url: str | None = None,
                 elapsed_seconds: float | None = None, page_bytes: int | None = None,
                 note: str | None = None):
        self.reachable = reachable
        self.failed = failed
        self.final_url = final_url
        self.elapsed_seconds = elapsed_seconds
        self.page_bytes = page_bytes
        self.note = note

    @property
    def fail_count(self) -> int:
        return len(self.failed)


async def probe_site(client: httpx.AsyncClient, domain: str, *,
                     timeout_seconds: float = 15.0,
                     slow_response_seconds: float = 3.0,
                     heavy_page_bytes: int = 2_000_000,
                     max_page_bytes: int = 3_000_000) -> ProbeResult:
    """Fetches the homepage once over https (falling back to http) and runs
    every check against that single response. One request per site: this is a
    scoring signal, not a crawl."""

    failed: list[str] = []
    https_ok = False
    response: httpx.Response | None = None
    elapsed = None
    notes: list[str] = []

    started = time.monotonic()
    try:
        response = await client.get(f"https://{domain}/", timeout=timeout_seconds,
                                    follow_redirects=True)
        https_ok = True
    except ssl.SSLError as exc:
        failed.append(BAD_CERT)
        notes.append(f"tls: {exc}")
    except httpx.TransportError as exc:
        notes.append(f"https: {exc}")

    if response is None:
        # No https at all - try plain http. A site that only answers on http
        # in 2026 is a finding in itself, not a fallback.
        try:
            response = await client.get(f"http://{domain}/", timeout=timeout_seconds,
                                        follow_redirects=True)
            if BAD_CERT not in failed:
                failed.append(NO_HTTPS)
        except httpx.TransportError as exc:
            notes.append(f"http: {exc}")
            return ProbeResult(False, [UNREACHABLE], note="; ".join(notes)[:400])

    elapsed = time.monotonic() - started

    if response.status_code >= 400:
        return ProbeResult(False, [UNREACHABLE],
                           final_url=str(response.url),
                           note=f"HTTP {response.status_code}")

    # http that never lands on https: check the scheme we ended up on, not the
    # one we asked for, so a proper 301 chain passes.
    if not https_ok and str(response.url).startswith("http://"):
        if NO_HTTPS_REDIRECT not in failed:
            failed.append(NO_HTTPS_REDIRECT)

    if elapsed is not None and elapsed > slow_response_seconds:
        failed.append(SLOW)

    body = response.content[:max_page_bytes]
    page_bytes = len(response.content)
    if page_bytes > heavy_page_bytes:
        failed.append(HEAVY)

    text = body.decode(response.encoding or "utf-8", errors="replace")
    if len(text.strip()) < 500:
        # A near-empty body usually means a JS-only shell or a parked domain;
        # either way the remaining HTML checks would all fire spuriously, so
        # report it once and stop.
        failed.append(EMPTY_PAGE)
        return ProbeResult(True, failed, final_url=str(response.url),
                           elapsed_seconds=elapsed, page_bytes=page_bytes,
                           note="; ".join(notes)[:400] or None)

    failed.extend(_html_checks(text, str(response.url)))

    return ProbeResult(True, failed, final_url=str(response.url),
                       elapsed_seconds=elapsed, page_bytes=page_bytes,
                       note="; ".join(notes)[:400] or None)


def _html_checks(html: str, final_url: str) -> list[str]:
    failed: list[str] = []
    soup = BeautifulSoup(html, "html.parser")

    if not soup.find("meta", attrs={"name": "viewport"}):
        failed.append(NO_VIEWPORT)

    title = soup.find("title")
    if not title or not title.get_text(strip=True):
        failed.append(NO_TITLE)

    description = soup.find("meta", attrs={"name": "description"})
    if not description or not (description.get("content") or "").strip():
        failed.append(NO_DESCRIPTION)

    if not soup.find("h1"):
        failed.append(NO_H1)

    if final_url.startswith("https://") and _has_mixed_content(soup):
        failed.append(MIXED_CONTENT)

    if not _has_favicon(soup):
        failed.append(NO_FAVICON)

    if not soup.find("meta", attrs={"property": "og:title"}):
        failed.append(NO_OG_TAGS)

    if _mostly_missing_alt(soup):
        failed.append(IMAGES_WITHOUT_ALT)

    if _uses_table_layout(soup):
        failed.append(TABLE_LAYOUT)

    return failed


def _has_favicon(soup: BeautifulSoup) -> bool:
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel") or []).lower()
        if "icon" in rel:
            return True
    return False


def _mostly_missing_alt(soup: BeautifulSoup) -> bool:
    """More than half the images carry no alt text.

    A couple of decorative images without alt is normal; a page where most of
    them lack it was built without anyone thinking about accessibility or
    image search, which is the kind of site worth rebuilding.
    """
    images = soup.find_all("img")
    if len(images) < 4:
        return False
    missing = sum(1 for img in images if not (img.get("alt") or "").strip())
    return missing > len(images) / 2


def _uses_table_layout(soup: BeautifulSoup) -> bool:
    """Nested tables holding the page together - a 2000s-era build.

    One table is likely a real data table, which is fine. A table containing
    another table is layout, and it means the site predates responsive design
    entirely.
    """
    for table in soup.find_all("table"):
        if table.find("table"):
            return True
    return False


def _has_mixed_content(soup: BeautifulSoup) -> bool:
    """An https page pulling scripts, styles or images over plain http - the
    browser shows a "not secure" warning, which clients do notice."""
    for tag, attr in (("script", "src"), ("link", "href"), ("img", "src"), ("iframe", "src")):
        for element in soup.find_all(tag):
            value = element.get(attr) or ""
            if value.startswith("http://"):
                return True
    return False
