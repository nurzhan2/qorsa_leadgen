"""Orchestrates one full pass: keyword -> paginated search -> parse ->
dedup -> map -> batch -> send to core. Stops once TARGET_PER_DAY companies
have been sent, so a single run never scrapes indefinitely.
"""

import structlog

from .core_client import CoreClient, RawCompanyRequest
from .dedup import DedupTracker
from .mapper import map_purchase_to_lead
from .parser import parse_search_html
from .zakupki_client import ZakupkiClient, ZakupkiClientError

log = structlog.get_logger(__name__)

BATCH_SIZE = 50


class ZakupkiRunner:
    def __init__(self, settings, keywords: list[str], client: ZakupkiClient, core: CoreClient):
        self._settings = settings
        self._keywords = keywords
        self._client = client
        self._core = core
        self._dedup = DedupTracker()

    async def run_once(self) -> dict:
        batch: list[RawCompanyRequest] = []
        sent = 0
        skipped_duplicates = 0
        skipped_no_customer = 0
        target = self._settings.target_per_day

        for keyword in self._keywords:
            if sent >= target:
                break

            for page in range(1, self._settings.max_pages_per_keyword + 1):
                if sent >= target:
                    break

                try:
                    html = await self._client.fetch_search_page(keyword, page)
                except ZakupkiClientError:
                    log.error("zakupki.giving_up_on_keyword", keyword=keyword, page=page)
                    break

                purchases = parse_search_html(html)
                if not purchases:
                    break  # no more results (or the page format changed - see README)

                for purchase in purchases:
                    reg_number = purchase.get("reg_number")
                    if not reg_number:
                        continue
                    if self._dedup.seen(reg_number):
                        skipped_duplicates += 1
                        continue
                    self._dedup.mark(reg_number)

                    lead = map_purchase_to_lead(purchase, keyword=keyword)
                    if lead is None:
                        skipped_no_customer += 1
                        continue

                    batch.append(lead)
                    sent += 1

                    if len(batch) >= BATCH_SIZE:
                        await self._send(batch)
                        batch = []

                    if sent >= target:
                        break

                log.info(
                    "zakupki.page_done",
                    keyword=keyword,
                    page=page,
                    found=len(purchases),
                    sent_so_far=sent,
                )

                if len(purchases) < self._settings.results_per_page:
                    break  # last page for this keyword

        await self._send(batch)
        return {
            "sent": sent,
            "skipped_duplicates": skipped_duplicates,
            "skipped_no_customer": skipped_no_customer,
            "seen_total": len(self._dedup),
        }

    async def _send(self, batch: list[RawCompanyRequest]) -> None:
        if not batch:
            return
        await self._core.send_leads(batch)
