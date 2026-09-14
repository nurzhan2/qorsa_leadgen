"""One run: pull a batch of pending companies from the core, enrich them
concurrently (bounded by a semaphore), PATCH every result back - including
"nothing found" - and repeat until the queue is empty or the batch budget
is spent.
"""

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from .core_client import CoreUnavailable, PendingCompany
from .enricher import FAILED, FOUND, NOTHING_FOUND, ROBOTS_BLOCKED, SKIPPED, UNAVAILABLE, EnrichOutcome

log = structlog.get_logger(__name__)


class EnrichRunner:
    def __init__(self, settings, core, enricher,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._settings = settings
        self._core = core
        self._enricher = enricher
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(settings.enrich_concurrency)

    async def run_once(self) -> dict:
        stats = {
            "companies": 0,
            FOUND: 0,
            NOTHING_FOUND: 0,
            UNAVAILABLE: 0,
            ROBOTS_BLOCKED: 0,
            SKIPPED: 0,
            FAILED: 0,
            "with_email": 0,
            "with_phone": 0,
            "with_messenger": 0,
            "foreign_email": 0,
            "patched": 0,
            "patch_failed": 0,
            "batches": 0,
            "pages_fetched": 0,
        }
        # Guards against re-processing within one run: if a PATCH failed, the
        # core still reports that company as pending and would hand it back
        # on the next batch - forever.
        seen: set[str] = set()
        aborted = None
        limit = self._settings.enrich_batch

        for _ in range(self._settings.max_batches_per_run):
            try:
                batch = await self._core.fetch_pending(limit)
            except CoreUnavailable as exc:
                aborted = str(exc)
                log.error("enrich.core_unavailable", error=aborted)
                break

            fresh = [company for company in batch if company.id not in seen]
            if not fresh:
                break
            seen.update(company.id for company in fresh)
            stats["batches"] += 1

            results = await asyncio.gather(*(self._process(company) for company in fresh))
            for outcome, patched in results:
                _tally(stats, outcome, patched)

            if len(batch) < limit:
                break  # queue drained

        summary = {**stats, "aborted": aborted}
        log.info("enrich.run_summary", **summary)
        print(
            f"ENRICH: обработано {stats['companies']} компаний; контакты найдены у {stats[FOUND]} "
            f"(email {stats['with_email']}, из них на чужом домене {stats['foreign_email']}; "
            f"телефон {stats['with_phone']}; мессенджер {stats['with_messenger']}); "
            f"ничего не найдено {stats[NOTHING_FOUND]}, сайт недоступен {stats[UNAVAILABLE]}, "
            f"robots.txt запретил {stats[ROBOTS_BLOCKED]}, пропущено {stats[SKIPPED]}, "
            f"ошибок воркера {stats[FAILED]}; не записано в ядро {stats['patch_failed']}"
        )
        return summary

    async def _process(self, company: PendingCompany) -> tuple[EnrichOutcome, bool]:
        async with self._semaphore:
            try:
                outcome = await self._enricher.enrich(company)
            except Exception as exc:  # noqa: BLE001 - one broken site must not stop the run
                log.exception("enrich.worker_error", company_id=company.id, domain=company.domain)
                # Still reported as attempted: otherwise a page that crashes the
                # parser would be re-crawled on every single run.
                outcome = EnrichOutcome(FAILED, notes=f"ошибка воркера: {type(exc).__name__}")

            try:
                patched = await self._core.patch_contacts(company.id, outcome.to_patch())
            except Exception:  # noqa: BLE001 - counted, never fatal
                log.exception("enrich.patch_error", company_id=company.id)
                patched = False

            log.info("enrich.company_done", company_id=company.id, domain=company.domain,
                     status=outcome.status, email=bool(outcome.email), phone=bool(outcome.phone),
                     messenger=bool(outcome.messenger), pages=outcome.pages_fetched,
                     patched=patched, notes=outcome.notes)
            # The pause between sites; held inside the semaphore so the slot
            # stays busy and concurrency really bounds the request rate.
            await self._sleep(self._settings.request_delay_seconds)
        return outcome, patched


def _tally(stats: dict, outcome: EnrichOutcome, patched: bool) -> None:
    stats["companies"] += 1
    stats[outcome.status] = stats.get(outcome.status, 0) + 1
    stats["pages_fetched"] += outcome.pages_fetched
    stats["with_email"] += bool(outcome.email)
    stats["with_phone"] += bool(outcome.phone)
    stats["with_messenger"] += bool(outcome.messenger)
    stats["foreign_email"] += outcome.foreign_email
    stats["patched" if patched else "patch_failed"] += 1
