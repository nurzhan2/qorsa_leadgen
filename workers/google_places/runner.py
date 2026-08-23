"""Orchestrates one full pass: city x category -> Text Search -> dedup ->
map -> batch -> send to core. Stops once TARGET_PER_DAY companies have
been sent, so a single run never runs up an unbounded Places API bill.
"""

import structlog

from .config import Category, City
from .core_client import CoreClient, RawCompanyRequest
from .dedup import DedupTracker
from .mapper import map_place_to_lead
from .places_client import GooglePlacesClient

log = structlog.get_logger(__name__)

BATCH_SIZE = 50


class GooglePlacesRunner:
    def __init__(
        self,
        settings,
        cities: list[City],
        categories: list[Category],
        client: GooglePlacesClient,
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

                text_query = f"{category.query} {city.name}"
                async for place in self._client.iter_places(text_query):
                    place_id = place.get("id")
                    if not place_id:
                        continue
                    if self._dedup.seen(place_id):
                        skipped_duplicates += 1
                        continue

                    lead = map_place_to_lead(place, city=city.name, category_name=category.name)
                    if lead is None:
                        skipped_no_name += 1
                        continue

                    self._dedup.mark(place_id)
                    batch.append(lead)
                    sent += 1

                    if len(batch) >= BATCH_SIZE:
                        await self._send(batch)
                        batch = []

                    if sent >= target:
                        break

                log.info(
                    "google_places.combo_done",
                    city=city.name,
                    category=category.name,
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
