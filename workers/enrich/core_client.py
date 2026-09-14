"""HTTP client for the Java core's enrichment boundary:

  GET   /api/v1/companies/pending-enrich?limit=N  -> companies to visit
  PATCH /api/v1/companies/{id}/contacts            -> what was found (fills blanks only)

The only way this worker touches the core.
"""

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

PENDING_PATH = "/api/v1/companies/pending-enrich"
CONTACTS_PATH = "/api/v1/companies/{id}/contacts"
REQUEST_TIMEOUT_SECONDS = 15.0


class PendingCompany(BaseModel):
    """Mirrors kz.qorsa.leadgen.web.dto.PendingEnrichCompany. A null
    email/phone/messenger is what the worker goes looking for."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str | None = None
    domain: str | None = None
    email: str | None = None
    phone: str | None = None
    messenger: str | None = None
    city: str | None = None


class ContactsPatch(BaseModel):
    """Mirrors kz.qorsa.leadgen.web.dto.ContactsPatchRequest; camelCase via
    alias, so `model_dump(by_alias=True, exclude_none=True)` is the body.
    All-None is valid: it still records the attempt."""

    model_config = ConfigDict(populate_by_name=True)

    email: str | None = None
    phone: str | None = None
    messenger: str | None = None
    enrich_notes: str | None = Field(default=None, alias="enrichNotes")


class CoreUnavailable(Exception):
    """The core could not be reached (or refused the request) after retries."""


class CoreServerError(Exception):
    """5xx from the core - retryable."""


class CoreClient:
    """Network errors and 5xx get up to retry_attempts tries with
    exponential backoff; 4xx (bad request, unknown id) are not retried.
    PATCH is safe to retry: the core only fills blanks, so applying the same
    report twice changes nothing."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None,
                 retry_attempts: int = 3, retry_base_seconds: float = 1.0, retry_max_seconds: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._retry_attempts = retry_attempts
        self._retry_base = retry_base_seconds
        self._retry_max = retry_max_seconds

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_pending(self, limit: int) -> list[PendingCompany]:
        try:
            response = await self._request("GET", PENDING_PATH, params={"limit": limit})
        except (httpx.TransportError, CoreServerError) as exc:
            raise CoreUnavailable(f"core unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise CoreUnavailable(f"core rejected pending-enrich: {response.status_code} {response.text[:200]}")
        return [PendingCompany.model_validate(item) for item in response.json()]

    async def patch_contacts(self, company_id: str, patch: ContactsPatch) -> bool:
        body = patch.model_dump(by_alias=True, exclude_none=True)
        try:
            response = await self._request("PATCH", CONTACTS_PATH.format(id=company_id), json=body)
        except (httpx.TransportError, CoreServerError) as exc:
            log.error("core.patch_failed", company_id=company_id, error=str(exc))
            return False
        if response.status_code >= 400:
            log.error("core.patch_rejected", company_id=company_id, status=response.status_code,
                      body=response.text[:300])
            return False
        return True

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        retryer = AsyncRetrying(
            retry=retry_if_exception_type((httpx.TransportError, CoreServerError)),
            wait=wait_exponential(multiplier=self._retry_base, min=self._retry_base, max=self._retry_max),
            stop=stop_after_attempt(self._retry_attempts),
            reraise=True,
        )
        return await retryer(self._request_once, method, path, kwargs)

    async def _request_once(self, method: str, path: str, kwargs: dict) -> httpx.Response:
        response = await self._client.request(method, f"{self._base_url}{path}", **kwargs)
        if response.status_code >= 500:
            log.warning("core.server_error", method=method, path=path, status=response.status_code)
            raise CoreServerError(f"{response.status_code} from core")
        return response
