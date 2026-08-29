"""Async client for the public Overpass API.

Overpass has no REST-style page/pageSize pagination - a query just returns
everything matching (or as much as the server's own limits allow). To keep
one (city, category) combo from pulling an unbounded amount of data, the
query itself caps output via Overpass QL's `out tags <N>;` limit; this
worker moves on to the next combo rather than trying to paginate deeper
into one.

The public instances rate-limit hard and answer 429/504 under load, so
retries use a long exponential backoff, every request pauses afterwards
regardless of outcome, and an instance that keeps saying "busy" is rotated
away from in favour of one of the public mirrors.

On the ethics of rotating: these mirrors are volunteer-run and exist so load
can spread across them. Rotating is not a way to multiply our quota - the
same pacing and the same backoff apply whichever endpoint is in use, and we
only move on when an instance has actively told us it's overloaded. See
README.md "Этика: Overpass - общий бесплатный ресурс".
"""

import asyncio

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

DEFAULT_ENDPOINTS = ("https://overpass-api.de/api/interpreter",)


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
        request_delay_seconds: float = 5.0,
        request_timeout_seconds: float = 75.0,
        page_size: int = 50,
        endpoints: list[str] | None = None,
        retry_attempts: int = 5,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 120.0,
    ):
        self._client = http_client
        self._user_agent = user_agent
        self._delay = request_delay_seconds
        self._timeout = request_timeout_seconds
        self._page_size = page_size
        self._endpoints = list(endpoints) if endpoints else list(DEFAULT_ENDPOINTS)
        self._endpoint_index = 0
        # Retry policy is per-instance rather than a module-level @retry
        # decorator, so it can be dialled down in tests. With the decorator a
        # single "endpoint is busy" test case took minutes of real sleeping,
        # which meant the failover path was effectively untestable.
        self._retry_attempts = retry_attempts
        self._retry_base = retry_base_seconds
        self._retry_max = retry_max_seconds
        #: Counts of 429/504 per endpoint, for the end-of-run summary.
        self.rate_limit_hits: dict[str, int] = {url: 0 for url in self._endpoints}
        self.endpoint_switches = 0

    @property
    def endpoint(self) -> str:
        return self._endpoints[self._endpoint_index]

    def _rotate_endpoint(self) -> bool:
        """Move to the next configured mirror. Returns False when there isn't
        another one to move to."""
        if len(self._endpoints) < 2:
            return False
        previous = self.endpoint
        self._endpoint_index = (self._endpoint_index + 1) % len(self._endpoints)
        self.endpoint_switches += 1
        log.warning(
            "osm.switching_endpoint",
            from_endpoint=previous,
            to_endpoint=self.endpoint,
            reason="the previous instance kept answering 429/504",
        )
        return True

    async def _execute(self, query: str) -> dict:
        """Retries the current endpoint with exponential backoff. Rotation
        between endpoints happens a level up, in fetch_elements, so a
        rotation restarts the backoff sequence rather than inheriting an
        already-long one."""
        retryer = AsyncRetrying(
            retry=retry_if_exception_type(
                (OverpassRateLimitError, OverpassServerError, httpx.TransportError)),
            wait=wait_exponential(
                multiplier=self._retry_base, min=self._retry_base, max=self._retry_max),
            stop=stop_after_attempt(self._retry_attempts),
            reraise=True,
        )
        return await retryer(self._execute_once, query)

    async def _execute_once(self, query: str) -> dict:
        """A single HTTP attempt against the CURRENT endpoint."""
        endpoint = self.endpoint
        try:
            response = await self._client.post(
                endpoint,
                data={"data": query},
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout,
            )
        finally:
            # Pause regardless of outcome - the public instances punish bursty
            # clients even when individual requests succeed.
            await asyncio.sleep(self._delay)

        if response.status_code in (429, 504):
            self.rate_limit_hits[endpoint] = self.rate_limit_hits.get(endpoint, 0) + 1
            log.warning("osm.rate_limited_or_timeout", status=response.status_code, endpoint=endpoint)
            raise OverpassRateLimitError(f"{response.status_code} from {endpoint}")
        if response.status_code >= 500:
            log.warning("osm.server_error", status=response.status_code, endpoint=endpoint)
            raise OverpassServerError(f"{response.status_code} from {endpoint}")
        if response.status_code >= 400:
            log.error("osm.client_error", status=response.status_code, endpoint=endpoint,
                      body=response.text[:300])
            raise OverpassClientError(f"{response.status_code} from {endpoint}: {response.text[:200]}")

        return response.json()

    async def fetch_elements(self, bbox: tuple[float, float, float, float], key: str, value: str | None) -> list[dict]:
        """Runs one combo's query, rotating to the next mirror if this one is
        persistently rate-limited. Each endpoint gets the full retry/backoff
        treatment before we give up on it, so we're never hopping away at the
        first 429 - only after an instance has made it clear it's overloaded.

        Returns [] rather than raising: one failed combo must not end the run,
        and the checkpoint deliberately does NOT mark it done, so a later run
        retries it.
        """
        query = build_query(bbox, key, value, limit=self._page_size, timeout=int(self._timeout) - 10)

        for _ in range(len(self._endpoints)):
            try:
                payload = await self._execute(query)
            except OverpassClientError:
                # A malformed query is our bug, not the server's - another
                # mirror would reject it identically.
                log.error("osm.giving_up_on_combo", key=key, value=value, reason="client_error")
                return []
            except (OverpassRateLimitError, OverpassServerError) as exc:
                if not self._rotate_endpoint():
                    log.error("osm.giving_up_on_combo", key=key, value=value,
                              reason="all endpoints exhausted", error=str(exc))
                    return []
                continue
            except httpx.TransportError as exc:
                if not self._rotate_endpoint():
                    log.error("osm.giving_up_on_combo", key=key, value=value,
                              reason="transport error, no other endpoint", error=str(exc))
                    return []
                continue
            return parse_elements(payload)

        log.error("osm.giving_up_on_combo", key=key, value=value, reason="every endpoint failed")
        return []
