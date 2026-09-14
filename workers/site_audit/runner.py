"""Orchestrates one audit pass: pull a batch of companies from the core,
measure each site (PSI where available, local probe always), report back.

Sites are independent, so companies within a batch run concurrently up to
`audit_concurrency`. PSI is the slow part - a real Lighthouse run per URL -
and is capped per run by `psi_max_per_run` so a scheduled job can't stall.
"""

import asyncio

import httpx
import structlog

from .checks import UNREACHABLE, ProbeResult, probe_site
from .core_client import AuditPatch, CoreClient, CoreUnavailable, PendingCompany
from .psi_client import PageSpeedClient, PageSpeedUnavailable

log = structlog.get_logger(__name__)


class AuditRunner:
    def __init__(self, settings, core: CoreClient, psi: PageSpeedClient, http: httpx.AsyncClient):
        self._settings = settings
        self._core = core
        self._psi = psi
        self._http = http
        self._psi_used = 0
        self._psi_lock = asyncio.Lock()

    async def run_once(self) -> dict:
        stats = {
            "companies": 0,
            "measured": 0,
            "unreachable": 0,
            "psi_scored": 0,
            "psi_skipped": 0,
            "patched": 0,
            "patch_failed": 0,
            "failed_checks_total": 0,
            "batches": 0,
            "aborted": None,
        }
        semaphore = asyncio.Semaphore(self._settings.audit_concurrency)

        for _ in range(self._settings.max_batches_per_run):
            try:
                batch = await self._core.fetch_pending(self._settings.audit_batch)
            except CoreUnavailable as exc:
                log.error("audit.core_unavailable", error=str(exc))
                stats["aborted"] = "core_unavailable"
                break

            if not batch:
                break

            stats["batches"] += 1
            results = await asyncio.gather(
                *(self._handle_company(company, semaphore) for company in batch),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    log.error("audit.company_crashed", error=str(result))
                    continue
                for key, value in result.items():
                    stats[key] = stats.get(key, 0) + value

        log.info("audit.run_summary", **stats)
        return stats

    async def _handle_company(self, company: PendingCompany, semaphore: asyncio.Semaphore) -> dict:
        async with semaphore:
            counters = {"companies": 1}
            domain = (company.domain or "").strip()
            if not domain:
                # The core's query already filters these out; belt and braces.
                return counters

            probe = await probe_site(
                self._http, domain,
                timeout_seconds=self._settings.request_timeout_seconds,
                slow_response_seconds=self._settings.slow_response_seconds,
                heavy_page_bytes=self._settings.heavy_page_bytes,
                max_page_bytes=self._settings.max_page_bytes,
            )

            pagespeed = None
            if probe.reachable:
                counters["measured"] = 1
                counters["failed_checks_total"] = probe.fail_count
                pagespeed, psi_note = await self._maybe_pagespeed(probe.final_url or f"https://{domain}/")
                if pagespeed is not None:
                    counters["psi_scored"] = 1
                elif psi_note:
                    counters["psi_skipped"] = 1
                    probe = _with_note(probe, psi_note)
            else:
                counters["unreachable"] = 1

            patch = _to_patch(probe, pagespeed)
            ok = await self._core.patch_audit(company.id, patch)
            counters["patched" if ok else "patch_failed"] = 1

            log.info(
                "audit.company_done",
                company_id=company.id,
                domain=domain,
                reachable=probe.reachable,
                pagespeed=pagespeed,
                fails=probe.fail_count,
                checks=probe.failed,
                patched=ok,
            )

            if self._settings.request_delay_seconds:
                await asyncio.sleep(self._settings.request_delay_seconds)
            return counters

    async def _maybe_pagespeed(self, url: str) -> tuple[int | None, str | None]:
        if not self._psi.enabled:
            return None, None

        async with self._psi_lock:
            if self._psi_used >= self._settings.psi_max_per_run:
                return None, "PSI: лимит прогона исчерпан"
            self._psi_used += 1

        try:
            return await self._psi.score(url), None
        except PageSpeedUnavailable as exc:
            log.warning("audit.psi_unavailable", url=url, error=str(exc))
            return None, f"PSI: {exc}"


def _with_note(probe: ProbeResult, extra: str) -> ProbeResult:
    note = f"{probe.note}; {extra}" if probe.note else extra
    return ProbeResult(probe.reachable, probe.failed, probe.final_url,
                       probe.elapsed_seconds, probe.page_bytes, note[:400])


def _to_patch(probe: ProbeResult, pagespeed: int | None) -> AuditPatch:
    """An unreachable site reports no auditFails count.

    Sending 1 there would be a lie in the direction that matters: the scoring
    rule reads auditFails as "this many things are wrong with the site", and a
    dead domain has no site to be wrong with. It is recorded as attempted with
    a note, and scores nothing.
    """
    if not probe.reachable:
        return AuditPatch(
            failed_checks=probe.failed or [UNREACHABLE],
            audit_notes=probe.note,
        )

    return AuditPatch(
        pagespeed=pagespeed,
        audit_fails=probe.fail_count,
        failed_checks=probe.failed or None,
        audit_notes=probe.note,
    )
