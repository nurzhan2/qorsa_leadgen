"""Orchestrates one full pass: city x category -> Overpass query -> dedup
-> map -> batch -> send to core. Stops once TARGET_PER_DAY companies have
been sent, so a single run never hammers the public Overpass instance
indefinitely.
"""

import structlog

from .config import Category, City
from .core_client import CoreClient, RawCompanyRequest
from .dedup import DedupTracker
from .mapper import map_element_to_lead
from .overpass_client import OverpassClient

log = structlog.get_logger(__name__)

BATCH_SIZE = 50


class OsmRunner:
    def __init__(
        self,
        settings,
        cities: list[City],
        categories: list[Category],
        client: OverpassClient,
        core: CoreClient,
    ):
        self._settings = settings
        self._cities = cities
        self._categories = categories
        self._client = client
        self._core = core
        self._dedup = DedupTracker()

    async def run_once(self) -> dict:
        batch: list[RawCompanyRequest] = []
        sent = 0
        skipped_duplicates = 0
        skipped_no_name = 0
        target = self._settings.target_per_day

        for city in self._cities:
            if sent >= target:
                break
            for category in self._categories:
                if sent >= target:
                    break

                elements = await self._client.fetch_elements(city.as_tuple, category.key, category.value)

                for element in elements:
                    osm_type = element.get("type")
                    osm_id = element.get("id")
                    if osm_id is None or self._dedup.seen(osm_type, osm_id):
                        if osm_id is not None:
                            skipped_duplicates += 1
                        continue

                    lead = map_element_to_lead(element, city=city.name, category_name=category.name)
                    if lead is None:
                        skipped_no_name += 1
                        continue

                    self._dedup.mark(osm_type, osm_id)
                    batch.append(lead)
                    sent += 1

                    if len(batch) >= BATCH_SIZE:
                        await self._send(batch)
                        batch = []

                    if sent >= target:
                        break

                log.info(
                    "osm.combo_done",
                    city=city.name,
                    category=category.name,
                    elements_fetched=len(elements),
                    sent_so_far=sent,
                    seen_total=len(self._dedup),
                )

        await self._send(batch)
        return {
            "sent": sent,
            "skipped_duplicates": skipped_duplicates,
            "skipped_no_name": skipped_no_name,
            "seen_total": len(self._dedup),
        }

    async def _send(self, batch: list[RawCompanyRequest]) -> None:
        if not batch:
            return
        await self._core.send_leads(batch)
