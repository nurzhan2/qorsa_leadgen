"""Async client for the public Overpass API (https://overpass-api.de).

Overpass has no REST-style page/pageSize pagination - a query just returns
everything matching (or as much as the server's own limits allow). To keep
one (city, category) combo from pulling an unbounded amount of data, the
query itself caps output via Overpass QL's `out tags <N>;` limit; this
worker moves on to the next combo rather than trying to paginate deeper
into one.

The public instance rate-limits hard and returns 429/504 under load, so
retries use a long exponential backoff, and every request pauses
afterwards regardless of outcome.
"""

import asyncio

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


class OverpassRateLimitError(Exception):
    """429 or 504 (Overpass's own "too busy" signals) - retryable."""


class OverpassServerError(Exception):
    """Other 5xx - retryable."""


class OverpassClientError(Exception):
    """Other 4xx / malformed query - NOT retryable."""


def build_query(bbox: tuple[float, float, float, float], key: str, value: str | None, limit: int, timeout: int = 60) -> str:
    """Pure: builds the Overpass QL string for one (bbox, tag) combo."""
    south, west, north, east = bbox
    tag_filter = f'["{key}"="{value}"]' if value else f'["{key}"]'
    bbox_clause = f"({south},{west},{north},{east})"
    return (
        f"[out:json][timeout:{timeout}];\n"
        f"(\n"
        f'  node{tag_filter}{bbox_clause};\n'
        f'  way{tag_filter}{bbox_clause};\n'
        f'  relation{tag_filter}{bbox_clause};\n'
        f");\n"
        f"out tags {limit};"
    )


def parse_elements(payload: dict) -> list[dict]:
    """Pure: pulls the elements list out of an Overpass JSON response.
    Defensive about missing keys - a malformed/empty response just yields
    no elements rather than raising."""
    return payload.get("elements") or []


class OverpassClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        user_agent: str,
        request_delay_seconds: float = 3.0,
        request_timeout_seconds: float = 75.0,
        page_size: int = 50,
    ):
        self._client = http_client
        self._user_agent = user_agent
        self._delay = request_delay_seconds
        self._timeout = request_timeout_seconds
        self._page_size = page_size

    @retry(
        retry=retry_if_exception_type((OverpassRateLimitError, OverpassServerError, httpx.TransportError)),
        wait=wait_exponential(multiplier=5, min=5, max=120),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _execute(self, query: str) -> dict:
        try:
            response = await self._client.post(
                OVERPASS_URL,
                data={"data": query},
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout,
            )
        finally:
            # Pause regardless of outcome - Overpass's public instance
            # punishes bursty clients even when individual requests succeed.
            await asyncio.sleep(self._delay)

        if response.status_code in (429, 504):
            log.warning("osm.rate_limited_or_timeout", status=response.status_code)
            raise OverpassRateLimitError(f"{response.status_code} from Overpass")
        if response.status_code >= 500:
            log.warning("osm.server_error", status=response.status_code)
            raise OverpassServerError(f"{response.status_code} from Overpass")
        if response.status_code >= 400:
            log.error("osm.client_error", status=response.status_code, body=response.text[:300])
            raise OverpassClientError(f"{response.status_code} from Overpass: {response.text[:200]}")

        return response.json()

    async def fetch_elements(self, bbox: tuple[float, float, float, float], key: str, value: str | None) -> list[dict]:
        query = build_query(bbox, key, value, limit=self._page_size, timeout=int(self._timeout) - 10)
        try:
            payload = await self._execute(query)
        except OverpassClientError:
            log.error("osm.giving_up_on_combo", key=key, value=value)
            return []
        return parse_elements(payload)
