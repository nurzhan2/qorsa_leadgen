"""PageSpeed Insights client.

PSI is free and does NOT require an activated billing account - it is a
different product from the Places API, whose free trial demands a prepayment.
Enable "PageSpeed Insights API" in the Google Cloud console, create an API
key, and that is all.

Without a key PSI still answers, but rate-limits anonymous callers almost
immediately (429 on the first request in practice), so this module treats a
missing key as "PSI disabled" rather than pretending it will work.
"""

import httpx
import structlog

log = structlog.get_logger(__name__)

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


class PageSpeedUnavailable(Exception):
    """PSI could not produce a score for this URL - quota, timeout, or a URL
    Lighthouse refused to load. Never fatal: the local probe still runs."""


class PageSpeedClient:
    def __init__(self, api_key: str, strategy: str = "mobile",
                 timeout_seconds: float = 90.0, client: httpx.AsyncClient | None = None):
        self._api_key = api_key
        self._strategy = strategy
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def score(self, url: str) -> int:
        """Lighthouse performance score as 0-100.

        PSI returns it as a 0..1 float; it is rounded here so the stored value
        matches what a human sees on the PSI web page and what the
        `pagespeed < 50` threshold in application.yml is written against.
        """
        if not self.enabled:
            raise PageSpeedUnavailable("no PSI_API_KEY configured")

        params = {
            "url": url,
            "strategy": self._strategy,
            "category": "performance",
            "key": self._api_key,
        }
        try:
            response = await self._client.get(PSI_ENDPOINT, params=params, timeout=self._timeout)
        except httpx.TransportError as exc:
            raise PageSpeedUnavailable(f"transport error: {exc}") from exc

        if response.status_code == 429:
            raise PageSpeedUnavailable("quota exceeded (429)")
        if response.status_code >= 400:
            # 400 here usually means Lighthouse could not load the page at all
            # - which is itself a finding, but the local probe reports it in a
            # form the scoring model understands, so don't invent a score.
            raise PageSpeedUnavailable(f"{response.status_code}: {response.text[:200]}")

        try:
            raw = response.json()["lighthouseResult"]["categories"]["performance"]["score"]
        except (KeyError, TypeError, ValueError) as exc:
            raise PageSpeedUnavailable(f"unexpected response shape: {exc}") from exc

        if raw is None:
            raise PageSpeedUnavailable("PSI returned a null performance score")
        return round(float(raw) * 100)
