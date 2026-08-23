"""Async client for the 2GIS Catalog/Places API (v3.0 "items" search
endpoint). Retries 429/5xx with exponential backoff via tenacity; a
semaphore plus a fixed per-request pause keep this worker gentle on
2GIS's rate-limited free tier.

Search is scoped by region_id (2GIS's numeric city id), NOT by folding the
city name into the free-text query: q="<rubric> <city name>" reliably
returns 0 results, while q="<rubric>"&region_id=<id> returns real data.
Confirmed manually against the live API - see cities.yml and README.md.
"""

import asyncio
import math

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

BASE_URL = "https://catalog.api.2gis.com/3.0/items"
RESPONSE_FIELDS = "items.contact_groups,items.address_name,items.full_name,items.point,items.rubrics"


class TwoGisRateLimitError(Exception):
    """429 - retryable."""


class TwoGisServerError(Exception):
    """5xx - retryable."""


class TwoGisClientError(Exception):
    """4xx other than 429 (bad key, bad params, ...) - NOT retryable."""


def parse_items(payload: dict) -> tuple[list[dict], int]:
    """Pure: pulls the items list and total count out of a 2GIS API JSON
    response. Defensive about missing keys - a malformed/empty response
    just yields no items rather than raising, so a single bad page can't
    crash the whole run."""
    result = payload.get("result") or {}
    items = result.get("items") or []
    total = result.get("total", len(items))
    return items, total


class TwoGisClient:
    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient,
        request_delay_seconds: float = 0.34,
        max_concurrent_requests: int = 2,
        page_size: int = 20,
        max_pages_per_combo: int = 3,
    ):
        self._api_key = api_key
        self._client = http_client
        self._delay = request_delay_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._page_size = page_size
        self._max_pages = max_pages_per_combo

    @retry(
        retry=retry_if_exception_type((TwoGisRateLimitError, TwoGisServerError, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _fetch_page(self, rubric_query: str, region_id: int, page: int) -> dict:
        async with self._semaphore:
            response = await self._client.get(
                BASE_URL,
                params={
                    "q": rubric_query,
                    "region_id": region_id,
                    "page": page,
                    "page_size": self._page_size,
                    "fields": RESPONSE_FIELDS,
                    "key": self._api_key,
                },
            )
        await asyncio.sleep(self._delay)

        if response.status_code == 429:
            log.warning("twogis.rate_limited", query=rubric_query, region_id=region_id, page=page)
            raise TwoGisRateLimitError(f"429 for {rubric_query!r} region_id={region_id} page {page}")
        if response.status_code >= 500:
            log.warning(
                "twogis.server_error", query=rubric_query, region_id=region_id, page=page,
                status=response.status_code,
            )
            raise TwoGisServerError(f"{response.status_code} for {rubric_query!r} region_id={region_id} page {page}")
        if response.status_code >= 400:
            log.error(
                "twogis.client_error",
                query=rubric_query,
                region_id=region_id,
                page=page,
                status=response.status_code,
                body=response.text[:300],
            )
            raise TwoGisClientError(
                f"{response.status_code} for {rubric_query!r} region_id={region_id} page {page}: "
                f"{response.text[:200]}"
            )

        return response.json()

    async def iter_items(self, region_id: int, rubric_query: str):
        """Yields items across pages for one (region_id, rubric) combo, up
        to max_pages_per_combo. Stops early once either a short page or
        the response's own `total` says there's nothing more to fetch."""
        for page in range(1, self._max_pages + 1):
            try:
                payload = await self._fetch_page(rubric_query, region_id, page)
            except TwoGisClientError:
                log.error("twogis.giving_up_on_combo", query=rubric_query, region_id=region_id, page=page)
                return

            items, total = parse_items(payload)
            if not items:
                return
            for item in items:
                yield item

            total_pages = math.ceil(total / self._page_size) if total else page
            if len(items) < self._page_size or page >= total_pages:
                return
