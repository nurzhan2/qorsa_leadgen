"""Async client for zakupki.gov.ru's public search results page
(there is no public JSON API for this search - see README.md). Retries
429/5xx with exponential backoff; paces requests to be a polite citizen of
a government site with no documented rate limit of its own.
"""

import asyncio

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

SEARCH_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html"


class ZakupkiRateLimitError(Exception):
    """429 - retryable."""


class ZakupkiServerError(Exception):
    """5xx - retryable."""


class ZakupkiClientError(Exception):
    """Other 4xx - NOT retryable."""


class ZakupkiClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        request_delay_seconds: float = 2.0,
        results_per_page: int = 10,
    ):
        self._client = http_client
        self._delay = request_delay_seconds
        self._results_per_page = results_per_page

    @retry(
        retry=retry_if_exception_type((ZakupkiRateLimitError, ZakupkiServerError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def fetch_search_page(self, keyword: str, page: int) -> str:
        """Returns the raw HTML of one search results page for a keyword.
        Empty string if the site rejects the request in a way that isn't
        worth retrying (see ZakupkiClientError)."""
        params = {
            "searchString": keyword,
            "morphology": "on",
            "pageNumber": str(page),
            "sortDirection": "false",
            "recordsPerPage": f"_{self._results_per_page}",
            "showLotsInfoHidden": "false",
        }

        try:
            response = await self._client.get(SEARCH_URL, params=params)
        finally:
            await asyncio.sleep(self._delay)

        if response.status_code == 429:
            log.warning("zakupki.rate_limited", keyword=keyword, page=page)
            raise ZakupkiRateLimitError(f"429 for {keyword!r} page {page}")
        if response.status_code >= 500:
            log.warning("zakupki.server_error", keyword=keyword, page=page, status=response.status_code)
            raise ZakupkiServerError(f"{response.status_code} for {keyword!r} page {page}")
        if response.status_code >= 400:
            log.error("zakupki.client_error", keyword=keyword, page=page, status=response.status_code)
            raise ZakupkiClientError(f"{response.status_code} for {keyword!r} page {page}")

        return response.text

    @retry(
        retry=retry_if_exception_type((ZakupkiRateLimitError, ZakupkiServerError, httpx.TransportError)),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def fetch_purchase_details(self, url: str) -> str:
        """Returns the raw HTML of one purchase's own detail/card page -
        a SECOND request per purchase, on top of the search page. Some
        notice links (e.g. .../ea20/view/...) 302-redirect to the site's
        canonical viewer (.../zk20/view/...); `follow_redirects=True`
        handles that transparently (verified live while building this).
        Gated by FETCH_DETAILS/MAX_DETAILS in runner.py precisely because
        this doubles (or worse) the request volume - see README.md
        "Два уровня запросов"."""
        try:
            response = await self._client.get(url, follow_redirects=True)
        finally:
            await asyncio.sleep(self._delay)

        if response.status_code == 429:
            log.warning("zakupki.rate_limited", url=url)
            raise ZakupkiRateLimitError(f"429 for {url}")
        if response.status_code >= 500:
            log.warning("zakupki.server_error", url=url, status=response.status_code)
            raise ZakupkiServerError(f"{response.status_code} for {url}")
        if response.status_code >= 400:
            log.error("zakupki.client_error", url=url, status=response.status_code)
            raise ZakupkiClientError(f"{response.status_code} for {url}")

        return response.text
