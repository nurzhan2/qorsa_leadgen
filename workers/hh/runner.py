"""Orchestrates one pass: keyword x area -> HH search -> filter -> group by
employer -> (optional) employer details -> map -> batch -> send to core.

Order of operations is deliberate:
  1. filter on what the SEARCH RESULT already tells us (name stop-lists,
     HH's accredited-IT flag) - free, no extra requests;
  2. group by employer, so a company with five vacancies is one lead;
  3. only THEN fetch employer details, once per surviving employer rather
     than once per vacancy. Filtering first is what keeps that second
     request count small.
"""

import structlog

from .core_client import CoreClient, RawCompanyRequest
from .dedup import EmployerAggregator
from .filters import EmployerFilter
from .hh_client import HhAuthError, HhClient
from .mapper import has_salary, map_employer_to_lead
from .staleness import age_days, is_stale

log = structlog.get_logger(__name__)

BATCH_SIZE = 50


class HhRunner:
    def __init__(self, settings, keywords: list[str], areas: list, client: HhClient,
                 core: CoreClient, employer_filter: EmployerFilter | None = None):
        self._settings = settings
        self._keywords = keywords
        self._areas = areas
        self._client = client
        self._core = core
        self._filter = employer_filter or EmployerFilter(
            agencies=settings.agencies,
            giants=settings.giants,
            skip_types=settings.skipped_employer_types,
            skip_accredited_it=settings.skip_accredited_it,
        )

    async def run_once(self) -> dict:
        stats = {
            "vacancies_seen": 0,
            "skipped_agency": 0,
            "skipped_giant": 0,
            "skipped_accredited_it": 0,
            "skipped_employer_type": 0,
            "skipped_no_employer": 0,
            "skipped_no_name": 0,
            "employers_found": 0,
            "stale_employers": 0,
            "with_salary": 0,
            "with_site": 0,
            "sent": 0,
            "employer_details_fetched": 0,
            "queries": 0,
        }
        aggregator = EmployerAggregator()
        stale_days = self._settings.stale_days
        aborted = None

        # --- pass 1: search, filter cheaply, group by employer ---
        for keyword in self._keywords:
            for area in self._areas:
                try:
                    vacancies = await self._client.iter_vacancies(
                        keyword, area.id, self._settings.max_pages_per_query)
                except HhAuthError as exc:
                    # Every subsequent request would fail identically, so
                    # stop rather than burning through the whole grid.
                    aborted = str(exc)
                    log.error("hh.aborting_run", reason="authorization", error=str(exc))
                    break
                stats["queries"] += 1
                stats["vacancies_seen"] += len(vacancies)

                for vacancy in vacancies:
                    employer = vacancy.get("employer") or {}
                    reason = self._filter.skip_reason(
                        name=employer.get("name"),
                        accredited_it=employer.get("accredited_it_employer"),
                    )
                    if reason is not None:
                        stats[_stat_key(reason[0])] += 1
                        log.debug("hh.employer_skipped", name=employer.get("name"),
                                  reason=reason[0], detail=reason[1])
                        continue

                    age = age_days(vacancy.get("published_at"))
                    aggregator.add(
                        vacancy,
                        age=age,
                        stale=is_stale(age, stale_days),
                        has_salary=has_salary(vacancy),
                        keyword=keyword,
                    )

                log.info("hh.query_done", keyword=keyword, area=area.name,
                         vacancies=len(vacancies), employers_so_far=len(aggregator))
            if aborted:
                break

        stats["skipped_no_employer"] = aggregator.skipped_no_employer
        stats["employers_found"] = len(aggregator)

        # --- pass 2: details + map + send, stalest employers first ---
        batch: list[RawCompanyRequest] = []
        target = self._settings.target_per_day

        for bucket in aggregator.buckets():
            if stats["sent"] >= target:
                log.info("hh.target_reached", target=target)
                break

            details = None
            if (self._settings.fetch_employer_details
                    and stats["employer_details_fetched"] < self._settings.max_employer_details):
                try:
                    details = await self._client.fetch_employer(bucket.employer_id)
                except HhAuthError as exc:
                    aborted = aborted or str(exc)
                    log.error("hh.aborting_details", error=str(exc))
                    break
                if details is not None:
                    stats["employer_details_fetched"] += 1

                # HH's own employer type is only knowable here - re-check.
                # An agency that slipped past the name list gets caught now.
                reason = self._filter.skip_reason(
                    name=(details or {}).get("name") or bucket.employer_name,
                    employer_type=(details or {}).get("type", {}).get("id")
                    if isinstance((details or {}).get("type"), dict)
                    else (details or {}).get("type"),
                )
                if reason is not None:
                    stats[_stat_key(reason[0])] += 1
                    log.debug("hh.employer_skipped_late", employer_id=bucket.employer_id,
                              reason=reason[0], detail=reason[1])
                    continue

            lead = map_employer_to_lead(bucket, stale_days=stale_days, employer_details=details)
            if lead is None:
                stats["skipped_no_name"] += 1
                continue

            if bucket.has_stale:
                stats["stale_employers"] += 1
            if bucket.has_salary:
                stats["with_salary"] += 1
            if lead.has_site:
                stats["with_site"] += 1

            batch.append(lead)
            stats["sent"] += 1
            if len(batch) >= BATCH_SIZE:
                await self._send(batch)
                batch = []

        await self._send(batch)

        summary = {
            **stats,
            "requests_made": self._client.requests_made,
            "rate_limit_hits": self._client.rate_limit_hits,
            "stale_threshold_days": stale_days,
            "aborted": aborted,
        }
        log.info("hh.run_summary", **summary)
        print(
            f"HH: собрано {stats['sent']} компаний "
            f"(из {stats['vacancies_seen']} вакансий, {stats['employers_found']} работодателей); "
            f"протухших: {stats['stale_employers']}, с зарплатой: {stats['with_salary']}, "
            f"отсеяно: агентств {stats['skipped_agency'] + stats['skipped_employer_type']}, "
            f"гигантов {stats['skipped_giant']}, IT-аккредитованных {stats['skipped_accredited_it']}"
        )
        return summary

    async def _send(self, batch: list[RawCompanyRequest]) -> None:
        if not batch:
            return
        await self._core.send_leads(batch)


def _stat_key(reason: str) -> str:
    return {
        "agency_name": "skipped_agency",
        "giant_name": "skipped_giant",
        "accredited_it": "skipped_accredited_it",
        "employer_type": "skipped_employer_type",
    }[reason]
