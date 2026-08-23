"""Orchestrates one full pass: fetch fresh domains from the configured
provider -> dedup -> check each for a real site -> map -> batch -> send to
core. Stops once TARGET_PER_DAY companies have been sent.
"""

import structlog

from .core_client import CoreClient, RawCompanyRequest
from .dedup import DedupTracker
from .mapper import map_domain_to_lead
from .providers import NewDomainsProvider
from .site_checker import check_has_site

log = structlog.get_logger(__name__)

BATCH_SIZE = 50


class NewDomainsRunner:
    def __init__(self, settings, provider: NewDomainsProvider, http_client, core: CoreClient):
        self._settings = settings
        self._provider = provider
        self._http_client = http_client
        self._core = core
        self._dedup = DedupTracker()

    async def run_once(self) -> dict:
        records = await self._provider.fetch_new_domains(self._http_client)
        log.info("newdomains.fetched", count=len(records))

        batch: list[RawCompanyRequest] = []
        sent = 0
        skipped_duplicates = 0
        target = self._settings.target_per_day

        for record in records:
            if sent >= target:
                break

            domain = record.get("domain")
            if not domain:
                continue
            if self._dedup.seen(domain):
                skipped_duplicates += 1
                continue
            self._dedup.mark(domain)

            has_site = await check_has_site(domain, self._http_client, timeout=self._settings.site_check_timeout_seconds)
            lead = map_domain_to_lead(record, has_site)
            if lead is None:
                continue

            batch.append(lead)
            sent += 1

            if len(batch) >= BATCH_SIZE:
                await self._send(batch)
                batch = []

        await self._send(batch)
        return {"sent": sent, "skipped_duplicates": skipped_duplicates, "fetched_total": len(records)}

    async def _send(self, batch: list[RawCompanyRequest]) -> None:
        if not batch:
            return
        await self._core.send_leads(batch)
