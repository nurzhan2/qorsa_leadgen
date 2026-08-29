"""Orchestrates one full pass: keyword -> paginated search -> parse ->
dedup -> (optionally) fetch each purchase's own detail page -> map ->
batch -> send to core. Stops once TARGET_PER_DAY companies have been sent,
so a single run never scrapes indefinitely.
"""

import structlog

from .core_client import CoreClient, RawCompanyRequest
from .dedup import DedupTracker
from .mapper import map_purchase_to_lead, merge_purchase_with_details
from .parser import parse_purchase_card, parse_search_html
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
        self._details_fetched = 0

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

                    details = await self._maybe_fetch_details(purchase)
                    enriched = merge_purchase_with_details(purchase, details)

                    lead = map_purchase_to_lead(enriched, keyword=keyword)
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
                    details_fetched=self._details_fetched,
                )

                if len(purchases) < self._settings.results_per_page:
                    break  # last page for this keyword

        await self._send(batch)
        return {
            "sent": sent,
            "skipped_duplicates": skipped_duplicates,
            "skipped_no_customer": skipped_no_customer,
            "seen_total": len(self._dedup),
            "details_fetched": self._details_fetched,
        }

    async def _maybe_fetch_details(self, purchase: dict) -> dict | None:
        """None means "didn't fetch" (disabled, cap reached, or no URL to
        fetch) - merge_purchase_with_details() falls back gracefully."""
        if not self._settings.fetch_details:
            return None
        if self._details_fetched >= self._settings.max_details_or_default():
            return None
        detail_url = purchase.get("detail_url")
        if not detail_url:
            return None

        self._details_fetched += 1
        try:
            html = await self._client.fetch_purchase_details(detail_url)
        except ZakupkiClientError:
            log.error("zakupki.details_fetch_failed", reg_number=purchase.get("reg_number"))
            return None
        # Passing the URL lets parse_purchase_card pick the 44-FZ/223-FZ
        # branch from the notice's own path (/223/...) instead of having to
        # sniff the markup - see parser.py _looks_like_223.
        return parse_purchase_card(html, url=detail_url)

    async def _send(self, batch: list[RawCompanyRequest]) -> None:
        if not batch:
            return
        await self._core.send_leads(batch)
