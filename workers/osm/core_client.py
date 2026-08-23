"""HTTP client for the network boundary into the Java core: POST
/api/v1/companies/ingest. This is the ONLY way this worker touches the
core - see the core's README.md for the full JSON contract this mirrors.
"""

import asyncio
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(__name__)

INGEST_PATH = "/api/v1/companies/ingest"
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 15.0


class RawCompanyRequest(BaseModel):
    """Mirrors kz.qorsa.leadgen.web.dto.RawCompanyRequest field-for-field
    (see the core README's JSON contract table). Field names use the
    core's exact camelCase via alias so `model_dump(by_alias=True)` is a
    drop-in POST body."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    domain: str | None = None
    phone: str | None = None
    email: str | None = None
    messenger: str | None = None
    address: str | None = None
    city: str | None = None
    source: str = "OSM"
    source_url: str | None = Field(default=None, alias="sourceUrl")
    has_site: bool = Field(default=True, alias="hasSite")
    raw: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    created: int = 0
    merged: int = 0
    leadsScored: int = 0
    hotCount: int = 0


class CoreClient:
    """Thin async wrapper around httpx with a small retry policy: network
    errors and 5xx responses get up to MAX_ATTEMPTS tries with exponential
    backoff; 4xx responses (bad payload) are not retried."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_leads(self, leads: list[RawCompanyRequest]) -> IngestResult | None:
        if not leads:
            return None

        payload = [lead.model_dump(by_alias=True, exclude_none=True) for lead in leads]
        url = f"{self._base_url}{INGEST_PATH}"

        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.post(url, json=payload)
            except httpx.TransportError as exc:
                last_error = exc
                log.warning("core_client.network_error", attempt=attempt, error=str(exc))
            else:
                if response.status_code < 400:
                    result = IngestResult.model_validate(response.json())
                    log.info(
                        "core_client.ingested",
                        created=result.created,
                        merged=result.merged,
                        hot_count=result.hotCount,
                        batch_size=len(leads),
                    )
                    return result

                if response.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        f"server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    log.warning("core_client.server_error", attempt=attempt, status=response.status_code)
                else:
                    log.error("core_client.rejected", status=response.status_code, body=response.text)
                    return None

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(2 ** (attempt - 1))

        log.error("core_client.failed", attempts=MAX_ATTEMPTS, error=str(last_error))
        return None
