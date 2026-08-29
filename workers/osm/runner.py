"""Orchestrates one run: a bounded slice of the (city x category) grid ->
Overpass query -> filter -> dedup -> map -> batch -> send to core.

Two limits bound a run, and they answer different questions:
  - TARGET_PER_DAY  - how many companies to ingest ("enough leads for now")
  - COMBOS_PER_RUN  - how many Overpass queries to make ("enough load on a
                      free shared service for now")
The second is the one that matters for being a good citizen: 30 cities x 36
categories is 1080 queries, so a run takes a slice, records it in the
checkpoint, and the next run continues from there. See README.md
"Режим постепенного обхода".
"""

import structlog

from .chains import ChainFilter
from .config import Category, City
from .core_client import CoreClient, RawCompanyRequest
from .dedup import DedupTracker
from .mapper import map_element_to_lead
from .overpass_client import OverpassClient
from .progress import ProgressTracker

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
        progress: ProgressTracker,
        chain_filter: ChainFilter | None = None,
    ):
        self._settings = settings
        self._cities = cities
        self._categories = categories
        self._client = client
        self._core = core
        self._progress = progress
        self._chains = chain_filter if chain_filter is not None else ChainFilter(settings.stoplist)
        self._dedup = DedupTracker()

    def all_combos(self) -> list[tuple[City, Category]]:
        """The full grid, city-major: one city's categories are crawled
        together so a partial run still gives usable coverage of somewhere,
        rather than a thin smear across everywhere."""
        return [(city, category) for city in self._cities for category in self._categories]

    async def run_once(self) -> dict:
        batch: list[RawCompanyRequest] = []
        stats = {
            "sent": 0,
            "elements_seen": 0,
            "skipped_duplicates": 0,
            "skipped_no_name": 0,
            "skipped_chains": 0,
            "with_phone": 0,
            "without_site": 0,
            "combos_processed": 0,
        }
        chains_hit: dict[str, int] = {}

        combos = self.all_combos()
        total_combos = len(combos)

        if self._settings.reset_progress:
            log.info("osm.progress_reset", reason="RESET_PROGRESS=true")
            self._progress.reset()
            self._progress.save()

        pending = self._progress.pending(combos)
        if not pending:
            # Grid complete - start the next sweep so a scheduled worker keeps
            # refreshing rather than going permanently idle.
            log.info(
                "osm.cycle_complete",
                combos=total_combos,
                cycle_started_at=self._progress.cycle_started_at,
                action="starting a new cycle",
            )
            self._progress.reset()
            self._progress.save()
            pending = self._progress.pending(combos)

        slice_ = pending[: self._settings.combos_per_run]
        already_done = total_combos - len(pending)
        log.info(
            "osm.run_plan",
            grid=total_combos,
            already_done=already_done,
            this_run=len(slice_),
            target_per_day=self._settings.target_per_day,
            endpoint=self._client.endpoint,
        )

        target = self._settings.target_per_day
        for city, category in slice_:
            if stats["sent"] >= target:
                log.info("osm.target_reached", target=target,
                         hint="remaining combos stay pending for the next run")
                break

            elements = await self._client.fetch_elements(city.as_tuple, category.key, category.value)
            stats["elements_seen"] += len(elements)

            for element in elements:
                osm_type = element.get("type")
                osm_id = element.get("id")
                if osm_id is None:
                    continue
                if self._dedup.seen(osm_type, osm_id):
                    stats["skipped_duplicates"] += 1
                    continue

                tags = element.get("tags") or {}
                name = tags.get("name")
                if not name:
                    stats["skipped_no_name"] += 1
                    continue

                matched_chain = self._chains.matched_chain(name)
                if matched_chain is not None:
                    stats["skipped_chains"] += 1
                    chains_hit[matched_chain] = chains_hit.get(matched_chain, 0) + 1
                    # Marked as seen so the same branch found under another
                    # category isn't re-examined and double-counted.
                    self._dedup.mark(osm_type, osm_id)
                    continue

                lead = map_element_to_lead(element, city=city.name, category_name=category.name)
                if lead is None:
                    stats["skipped_no_name"] += 1
                    continue

                self._dedup.mark(osm_type, osm_id)
                if lead.phone:
                    stats["with_phone"] += 1
                if not lead.has_site:
                    stats["without_site"] += 1

                batch.append(lead)
                stats["sent"] += 1

                if len(batch) >= BATCH_SIZE:
                    await self._send(batch)
                    batch = []

                if stats["sent"] >= target:
                    break

            # Marked done only after the combo was actually processed. A combo
            # whose query failed is left pending on purpose, so a later run
            # retries it instead of silently leaving a hole in the grid.
            self._progress.mark_done(city.name, category.name)
            # Saved per combo, not at the end: an interrupted run must not
            # re-pay for queries it already made.
            self._progress.save()
            stats["combos_processed"] += 1

            log.info(
                "osm.combo_done",
                city=city.name,
                category=category.name,
                elements_fetched=len(elements),
                sent_so_far=stats["sent"],
                progress=f"{self._progress.done_count}/{total_combos}",
            )

        await self._send(batch)

        remaining = total_combos - self._progress.done_count
        next_up = None
        still_pending = self._progress.pending(combos)
        if still_pending:
            next_city, next_category = still_pending[0]
            next_up = f"{next_city.name} / {next_category.name}"

        log.info(
            "osm.run_summary",
            combos_processed=stats["combos_processed"],
            grid_progress=f"{self._progress.done_count}/{total_combos}",
            remaining_combos=remaining,
            next_run_starts_with=next_up or "новый цикл с начала сетки",
            collected=stats["sent"],
            elements_seen=stats["elements_seen"],
            skipped_no_name=stats["skipped_no_name"],
            skipped_chains=stats["skipped_chains"],
            skipped_duplicates=stats["skipped_duplicates"],
            with_phone=stats["with_phone"],
            without_site=stats["without_site"],
            endpoint_switches=self._client.endpoint_switches,
            rate_limit_hits=self._client.rate_limit_hits,
        )
        if chains_hit:
            log.info("osm.chains_filtered",
                     **{name: count for name, count in sorted(
                         chains_hit.items(), key=lambda kv: kv[1], reverse=True)[:10]})

        print(
            f"Обработано {self._progress.done_count}/{total_combos} пар "
            f"(за этот прогон {stats['combos_processed']}), "
            f"следующий запуск продолжит с: {next_up or 'начала нового цикла'}"
        )

        return {
            **stats,
            "grid_total": total_combos,
            "grid_done": self._progress.done_count,
            "remaining_combos": remaining,
            "next_run_starts_with": next_up,
            "seen_total": len(self._dedup),
            "endpoint_switches": self._client.endpoint_switches,
        }

    async def _send(self, batch: list[RawCompanyRequest]) -> None:
        if not batch:
            return
        await self._core.send_leads(batch)
