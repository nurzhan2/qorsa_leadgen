"""Async client for Google Places API (New) - Text Search
(POST /v1/places:searchText).

The FieldMask below requests exactly what's needed for a lead
(id/displayName/formattedAddress/nationalPhoneNumber/websiteUri/types)
directly from Text Search, so a separate Place Details call per result
isn't needed - that would double the request count (and cost) for no
benefit here.

Note on cost: nationalPhoneNumber and websiteUri are "Contact Data" fields
in Places API (New)'s SKU-based pricing, billed above the bare-bones
"Essentials" tier (id/displayName/formattedAddress). See README.md.
"""

import asyncio

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.websiteUri,places.types,nextPageToken"
)


class GooglePlacesRateLimitError(Exception):
    """429 - retryable."""


class GooglePlacesServerError(Exception):
    """5xx - retryable."""


class GooglePlacesClientError(Exception):
    """Other 4xx (bad key, billing disabled, invalid request, ...) - NOT retryable."""


def parse_places(payload: dict) -> tuple[list[dict], str | None]:
    """Pure: pulls the places list and next page token out of a Places
    API (New) JSON response. Defensive about missing keys."""
    places = payload.get("places") or []
    next_token = payload.get("nextPageToken")
    return places, next_token


class GooglePlacesClient:
    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient,
        request_delay_seconds: float = 1.0,
        page_token_delay_seconds: float = 2.0,
        max_pages_per_query: int = 3,
    ):
        self._api_key = api_key
        self._client = http_client
        self._delay = request_delay_seconds
        self._page_token_delay = page_token_delay_seconds
        self._max_pages = max_pages_per_query

    @retry(
        retry=retry_if_exception_type((GooglePlacesRateLimitError, GooglePlacesServerError, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _search(self, text_query: str, page_token: str | None) -> dict:
        body: dict = {"textQuery": text_query}
        if page_token:
            body["pageToken"] = page_token

        response = await self._client.post(
            TEXT_SEARCH_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
        )
        await asyncio.sleep(self._delay)

        if response.status_code == 429:
            log.warning("google_places.rate_limited", query=text_query)
            raise GooglePlacesRateLimitError(f"429 for {text_query!r}")
        if response.status_code >= 500:
            log.warning("google_places.server_error", query=text_query, status=response.status_code)
            raise GooglePlacesServerError(f"{response.status_code} for {text_query!r}")
        if response.status_code >= 400:
            log.error(
                "google_places.client_error",
                query=text_query,
                status=response.status_code,
                body=response.text[:500],
            )
            raise GooglePlacesClientError(f"{response.status_code} for {text_query!r}: {response.text[:300]}")

        return response.json()

    async def iter_places(self, text_query: str):
        """Yields places across pages for one text query, up to
        max_pages_per_query. A fresh nextPageToken from Google isn't
        immediately valid, so this pauses page_token_delay_seconds before
        using one."""
        page_token = None
        for _page in range(1, self._max_pages + 1):
            try:
                payload = await self._search(text_query, page_token)
            except GooglePlacesClientError:
                log.error("google_places.giving_up_on_query", query=text_query)
                return

            places, next_token = parse_places(payload)
            for place in places:
                yield place

            if not next_token or not places:
                return
            page_token = next_token
            await asyncio.sleep(self._page_token_delay)
