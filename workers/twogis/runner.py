"""Orchestrates one full pass: city x rubric -> paginated fetch -> dedup ->
map -> batch -> send to core. Stops once TARGET_PER_DAY companies have been
sent, so a single run never drains the whole day's API quota on its own.
"""

import structlog

from .core_client import CoreClient, RawCompanyRequest
from .dedup import DedupTracker
from .mapper import map_item_to_lead
from .twogis_client import TwoGisClient

log = structlog.get_logger(__name__)

BATCH_SIZE = 50


class TwoGisRunner:
    def __init__(self, settings, cities: list[str], rubrics: list, client: TwoGisClient, core: CoreClient):
        self._settings = settings
        self._cities = cities
        self._rubrics = rubrics
        self._client = client
        self._core = core
        self._dedup = DedupTracker()

    async def run_once(self) -> dict:
        batch: list[RawCompanyRequest] = []
        sent = 0
        skipped_duplicates = 0
        target = self._settings.target_per_day

        for city in self._cities:
            if sent >= target:
                break
            for rubric in self._rubrics:
                if sent >= target:
                    break

                async for item in self._client.iter_items(city, rubric.query):
                    twogis_id = str(item.get("id") or "")
                    if not twogis_id:
                        continue
                    if self._dedup.seen(twogis_id):
                        skipped_duplicates += 1
                        continue
                    self._dedup.mark(twogis_id)

                    batch.append(map_item_to_lead(item, city=city, rubric_name=rubric.name))
                    sent += 1

                    if len(batch) >= BATCH_SIZE:
                        await self._send(batch)
                        batch = []

                    if sent >= target:
                        break

                log.info(
                    "twogis.combo_done",
                    city=city,
                    rubric=rubric.name,
                    sent_so_far=sent,
                    seen_total=len(self._dedup),
                )

        await self._send(batch)
        return {"sent": sent, "skipped_duplicates": skipped_duplicates, "seen_total": len(self._dedup)}

    async def _send(self, batch: list[RawCompanyRequest]) -> None:
        if not batch:
            return
        await self._core.send_leads(batch)
