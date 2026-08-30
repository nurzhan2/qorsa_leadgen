"""Async client for the official HH API (https://api.hh.ru).

Everything here follows HH's own OpenAPI spec
(https://api.hh.ru/openapi/specification/public), read while building this:

  - GET /vacancies  - `text`, `area`, `page`, `per_page` (max 100),
    `order_by`. Result depth is capped at **2000**: "глубина возвращаемых
    результатов не может быть больше 2000", so per_page=100 allows pages
    0..19 and page 20 is an error. paginate() enforces that itself rather
    than discovering it via a 400.
  - GET /employers/{id} - carries `site_url` and `type`; the search result
    does NOT, which is why employer details are a separate opt-in request.

AUTHORIZATION. HH's spec says of /vacancies: "Если не передан токен
авторизации, то после первого запроса будет предложено пройти капчу."
Anonymous use is therefore not viable for an unattended worker, and was
confirmed dead in testing (403 `{"type":"forbidden"}` from HH's ddos-guard
edge on every request, with every User-Agent). Set HH_TOKEN. See README.md.
"""

import asyncio

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

#: HH caps search depth at this many results, regardless of paging.
MAX_RESULT_DEPTH = 2000
#: HH's documented maximum for per_page.
MAX_PER_PAGE = 100


class HhRateLimitError(Exception):
    """429 - retryable."""


class HhServerError(Exception):
    """5xx - retryable."""


class HhAuthError(Exception):
    """401/403 - NOT retryable. Almost always a missing/expired HH_TOKEN."""


class HhClientError(Exception):
    """Other 4xx - NOT retryable (bad query)."""


def max_page(per_page: int) -> int:
    """Highest 0-based page number HH will serve for this page size.

    per_page=100 -> 19 (results 1901..2000). Requesting page 20 returns an
    error, so the caller stops here instead of walking into it.
    """
    per_page = max(1, min(int(per_page), MAX_PER_PAGE))
    return max(0, (MAX_RESULT_DEPTH // per_page) - 1)


class HhClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        user_agent: str,
        token: str = "",
        base_url: str = "https://api.hh.ru",
        request_delay_seconds: float = 1.0,
        request_timeout_seconds: float = 30.0,
        per_page: int = 100,
        retry_attempts: int = 4,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 60.0,
    ):
        self._client = http_client
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._token = (token or "").strip()
        self._delay = request_delay_seconds
        self._timeout = request_timeout_seconds
        self._per_page = max(1, min(int(per_page), MAX_PER_PAGE))
        # Per-instance rather than a module-level @retry decorator so tests
        # can collapse the waits; the failure paths are otherwise untestable
        # in reasonable time.
        self._retry_attempts = retry_attempts
        self._retry_base = retry_base_seconds
        self._retry_max = retry_max_seconds

        self.requests_made = 0
        self.rate_limit_hits = 0

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict:
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _get(self, path: str, params: dict | None = None) -> dict:
        retryer = AsyncRetrying(
            retry=retry_if_exception_type((HhRateLimitError, HhServerError, httpx.TransportError)),
            wait=wait_exponential(multiplier=self._retry_base, min=self._retry_base,
                                  max=self._retry_max),
            stop=stop_after_attempt(self._retry_attempts),
            reraise=True,
        )
        return await retryer(self._get_once, path, params)

    async def _get_once(self, path: str, params: dict | None) -> dict:
        url = f"{self._base_url}{path}"
        try:
            response = await self._client.get(
                url, params=params, headers=self._headers(), timeout=self._timeout)
            self.requests_made += 1
        finally:
            # Pause regardless of outcome - pacing is about the rate of
            # requests we make, not the rate of ones that succeed.
            await asyncio.sleep(self._delay)

        status = response.status_code
        if status == 429:
            self.rate_limit_hits += 1
            log.warning("hh.rate_limited", path=path)
            raise HhRateLimitError("429 from HH")
        if status in (401, 403):
            # Retrying will not fix credentials, and hammering an endpoint
            # that just refused us is exactly what gets an IP blocked.
            log.error("hh.forbidden", path=path, status=status,
                      body=response.text[:200], has_token=self.has_token)
            raise HhAuthError(
                f"{status} from HH - set HH_TOKEN (see README 'Доступ к API'); body: "
                f"{response.text[:160]}")
        if status >= 500:
            log.warning("hh.server_error", path=path, status=status)
            raise HhServerError(f"{status} from HH")
        if status >= 400:
            log.error("hh.client_error", path=path, status=status, body=response.text[:200])
            raise HhClientError(f"{status} from HH: {response.text[:160]}")

        return response.json()

    async def search_vacancies(self, text: str, area: str, page: int = 0) -> dict:
        """One page of GET /vacancies. Returns the raw payload
        ({found, pages, page, per_page, items}) - parsing stays in mapper.py."""
        return await self._get("/vacancies", {
            "text": text,
            "area": area,
            "page": page,
            "per_page": self._per_page,
            # Oldest-first would be ideal for stale hunting, but HH offers
            # only publication_time (newest first). Staleness is computed
            # locally from published_at instead - see staleness.py.
            "order_by": "publication_time",
        })

    async def iter_vacancies(self, text: str, area: str, max_pages: int) -> list[dict]:
        """Walks pages for one (keyword, area) pair, stopping at whichever
        comes first: max_pages, the reported page count, an empty page, or
        HH's hard 2000-result depth limit.

        Returns [] rather than raising on a bad query so one keyword can't
        kill the run - but an auth error IS propagated, because it means
        every subsequent request would fail too.
        """
        collected: list[dict] = []
        ceiling = min(max_pages, max_page(self._per_page) + 1)
        if ceiling < max_pages:
            log.debug("hh.depth_capped", text=text, area=area,
                      requested_pages=max_pages, allowed_pages=ceiling,
                      reason=f"HH caps result depth at {MAX_RESULT_DEPTH}")

        for page in range(ceiling):
            try:
                payload = await self.search_vacancies(text, area, page)
            except HhAuthError:
                raise
            except (HhClientError, HhRateLimitError, HhServerError, httpx.TransportError) as exc:
                log.error("hh.giving_up_on_query", text=text, area=area, page=page, error=str(exc))
                break

            items = payload.get("items") or []
            collected.extend(items)
            if page == 0:
                log.debug("hh.query_start", text=text, area=area,
                          found=payload.get("found"), pages=payload.get("pages"))
            if not items:
                break
            total_pages = payload.get("pages")
            if isinstance(total_pages, int) and page + 1 >= total_pages:
                break

        return collected

    async def fetch_employer(self, employer_id: str) -> dict | None:
        """GET /employers/{id} - the only place `site_url` and the employer
        `type` live. None on failure; the lead is still usable without them."""
        if not employer_id:
            return None
        try:
            return await self._get(f"/employers/{employer_id}", None)
        except HhAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - one employer must not stop the run
            log.warning("hh.employer_fetch_failed", employer_id=employer_id, error=str(exc))
            return None
