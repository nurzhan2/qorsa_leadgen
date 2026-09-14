"""Polite HTTP against other people's websites.

Every page request goes through fetch_page(), which, for every hop of a
redirect chain:
  1. refuses hosts we must never touch (IP literals, localhost, names that
     resolve into a private network) - domains/redirects come from third
     parties, see domains.resolve_guard();
  2. reads that origin's /robots.txt once (cached per origin for the run)
     and raises RobotsDisallowed instead of requesting a forbidden path;
  3. waits so that two requests to the same origin are at least
     REQUEST_DELAY_SECONDS apart (or the site's Crawl-delay, if larger,
     up to a cap);
  4. sends an honest User-Agent, reads at most max_page_bytes of an HTML
     body and nothing at all of anything else.

Redirects are followed by hand (not by httpx) precisely so that steps 1-3
apply to the redirect TARGET too: romashka.kz -> romashka-group.com is a
different site with its own robots.txt.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from .domains import resolve_guard
from .extractor import decode_html
from .robots import MAX_ROBOTS_BYTES, RobotsRules, allow_all, rules_for_status

log = structlog.get_logger(__name__)

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: RFC 9309 asks crawlers to follow at least five redirects for robots.txt;
#: the same bound is used for pages.
MAX_REDIRECTS = 5
_NOT_CHECKED = allow_all("robots.txt не проверялся (RESPECT_ROBOTS=false)")


class FetchError(Exception):
    """Base for everything fetch_page() raises."""


class SiteUnavailable(FetchError):
    """DNS/connection/TLS failure, timeout, too many redirects."""


class BlockedHost(FetchError):
    """A host we refuse to contact at all (see domains.resolve_guard)."""


class RobotsDisallowed(FetchError):
    def __init__(self, url: str, detail: str):
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


@dataclass(frozen=True)
class Page:
    url: str           # final URL, after redirects
    status: int
    content_type: str
    html: str | None   # None for errors and non-HTML responses


def product_token(user_agent: str) -> str:
    """"qorsa-leadgen-enrich/1.0 (...)" -> "qorsa-leadgen-enrich": the name a
    robots.txt `User-agent:` line would use to address this bot."""
    token = (user_agent or "").strip().split(" ", 1)[0].split("/", 1)[0]
    return token.lower() or "*"


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        respect_robots: bool = True,
        request_delay_seconds: float = 2.0,
        max_crawl_delay_seconds: float = 30.0,
        request_timeout_seconds: float = 15.0,
        max_page_bytes: int = 2_000_000,
        host_guard: Callable[[str], Awaitable[str | None]] = resolve_guard,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._client = client
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "ru,kk;q=0.9,en;q=0.7",
        }
        self._token = product_token(user_agent)
        self._respect_robots = respect_robots
        self._delay = request_delay_seconds
        self._max_crawl_delay = max_crawl_delay_seconds
        self._timeout = request_timeout_seconds
        self._max_page_bytes = max_page_bytes
        # Injectable so tests neither wait nor touch DNS.
        self._host_guard = host_guard
        self._sleep = sleep
        self._clock = clock

        self._robots: dict[str, RobotsRules] = {}
        self._guard_cache: dict[str, str | None] = {}
        self._last_hit: dict[str, float] = {}
        self.requests_made = 0

    async def fetch_page(self, url: str) -> Page:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            host = (parts.hostname or "").lower()
            if parts.scheme not in ("http", "https") or not host:
                raise SiteUnavailable(f"неподдерживаемый адрес: {current[:120]}")
            await self._check_host(host)

            origin = f"{parts.scheme}://{parts.netloc.lower()}"
            rules = await self.robots_for(origin)
            target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            rule = rules.blocking_rule(target)
            if rule is not None:
                raise RobotsDisallowed(current, rules.detail or f"Disallow: {rule}")

            await self._pace(origin, rules.crawl_delay)
            status, headers, body = await self._get(
                current, max_bytes=self._max_page_bytes,
                want_body=lambda code, ctype: 200 <= code < 300 and _is_html(ctype))

            location = headers.get("location")
            if status in REDIRECT_STATUSES and location:
                current = urljoin(current, location)
                continue

            content_type = headers.get("content-type", "")
            html = decode_html(body, _charset(content_type)) if status < 400 and _is_html(content_type) else None
            return Page(url=current, status=status, content_type=content_type, html=html)

        raise SiteUnavailable(f"больше {MAX_REDIRECTS} редиректов")

    async def robots_for(self, origin: str) -> RobotsRules:
        """The rules for one origin (scheme://host[:port]), fetched once and
        cached for the life of this fetcher - i.e. one run."""
        if not self._respect_robots:
            return _NOT_CHECKED
        cached = self._robots.get(origin)
        if cached is not None:
            return cached
        rules = await self._fetch_robots(origin)
        self._robots[origin] = rules
        log.debug("enrich.robots", origin=origin, disallows=len(rules.disallows),
                  crawl_delay=rules.crawl_delay, detail=rules.detail or None)
        return rules

    async def _fetch_robots(self, origin: str) -> RobotsRules:
        url = f"{origin}/robots.txt"
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                return allow_all("robots.txt: редирект на неподдерживаемый адрес — считаем отсутствующим")
            await self._check_host(parts.hostname.lower())
            await self._pace(f"{parts.scheme}://{parts.netloc.lower()}", None)
            # A transport error propagates as SiteUnavailable: a site whose
            # robots.txt we cannot even connect to is a site we cannot reach.
            status, headers, body = await self._get(
                url, max_bytes=MAX_ROBOTS_BYTES, want_body=lambda code, _ctype: 200 <= code < 300)
            location = headers.get("location")
            if status in REDIRECT_STATUSES and location:
                url = urljoin(url, location)
                continue
            return rules_for_status(status, body, self._token)
        # RFC 9309 2.3.1.2: more than five redirects -> MAY treat as unavailable.
        return allow_all("robots.txt: больше 5 редиректов — считаем отсутствующим")

    async def _check_host(self, host: str) -> None:
        if host not in self._guard_cache:
            self._guard_cache[host] = await self._host_guard(host)
        reason = self._guard_cache[host]
        if reason:
            raise BlockedHost(reason)

    async def _pace(self, origin: str, crawl_delay: float | None) -> None:
        delay = self._delay
        if crawl_delay:
            delay = max(delay, min(crawl_delay, self._max_crawl_delay))
        last = self._last_hit.get(origin)
        if last is not None:
            wait = last + delay - self._clock()
            if wait > 0:
                await self._sleep(wait)
        self._last_hit[origin] = self._clock()

    async def _get(self, url: str, *, max_bytes: int,
                   want_body: Callable[[int, str], bool]) -> tuple[int, httpx.Headers, bytes]:
        try:
            async with self._client.stream("GET", url, headers=self._headers, follow_redirects=False,
                                           timeout=self._timeout) as response:
                self.requests_made += 1
                body = b""
                if want_body(response.status_code, response.headers.get("content-type", "")):
                    body = await _read_capped(response, max_bytes)
                return response.status_code, response.headers, body
        except httpx.TimeoutException as exc:
            raise SiteUnavailable(f"таймаут ({type(exc).__name__})") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise SiteUnavailable(_describe(exc)) from exc


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= max_bytes:
            break
    return b"".join(chunks)[:max_bytes]


def _is_html(content_type: str) -> bool:
    value = (content_type or "").lower()
    # No Content-Type at all: sniffing is what a browser would do, and the
    # extractor copes fine with non-HTML garbage.
    return not value.strip() or "text/html" in value or "application/xhtml" in value


_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w.:-]+)", re.IGNORECASE)


def _charset(content_type: str) -> str | None:
    match = _CHARSET_RE.search(content_type or "")
    return match.group(1) if match else None


def _describe(exc: Exception) -> str:
    message = str(exc).strip()
    text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return text[:160]
