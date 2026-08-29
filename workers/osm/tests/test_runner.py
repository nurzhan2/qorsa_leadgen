"""Tests for OsmRunner: the bounded-slice crawl, checkpoint integration,
chain filtering and the end-of-run statistics. Fake Overpass client and fake
core - no network.
"""

import pytest

from workers.osm.chains import ChainFilter
from workers.osm.config import Category, City, Settings
from workers.osm.progress import ProgressTracker, load_progress
from workers.osm.runner import OsmRunner


class FakeOverpass:
    """Records every combo it was asked for, and returns canned elements."""

    def __init__(self, elements_by_key=None, default_elements=None):
        self._by_key = elements_by_key or {}
        self._default = default_elements if default_elements is not None else []
        self.calls: list[tuple] = []
        self.endpoint = "https://fake/api/interpreter"
        self.endpoint_switches = 0
        self.rate_limit_hits: dict[str, int] = {}

    async def fetch_elements(self, bbox, key, value):
        self.calls.append((bbox, key, value))
        return list(self._by_key.get((key, value), self._default))


class FakeCore:
    def __init__(self):
        self.sent: list = []

    async def send_leads(self, batch):
        self.sent.extend(batch)

    async def aclose(self):
        pass


def node(osm_id: int, name: str | None, **tags):
    element_tags = dict(tags)
    if name is not None:
        element_tags["name"] = name
    return {"type": "node", "id": osm_id, "tags": element_tags}


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        progress_file=tmp_path / "osm_progress.json",
        combos_per_run=50,
        target_per_day=1000,
        reset_progress=False,
    )
    values.update(overrides)
    return Settings(**values)


CITIES = [
    City(name="Москва", bbox=[55.5, 37.2, 55.9, 38.0]),
    City(name="Казань", bbox=[55.6, 48.8, 55.9, 49.3]),
]
CATEGORIES = [
    Category(name="Кафе", key="amenity", value="cafe"),
    Category(name="Аптеки", key="amenity", value="pharmacy"),
]


def build(tmp_path, overpass, settings=None, progress=None, stoplist=None):
    settings = settings or make_settings(tmp_path)
    progress = progress if progress is not None else load_progress(settings.progress_file)
    return OsmRunner(
        settings, CITIES, CATEGORIES, overpass, FakeCore(), progress,
        ChainFilter(stoplist if stoplist is not None else []),
    )


# --- the grid & the bounded slice ----------------------------------------


def test_grid_is_every_city_times_every_category(tmp_path):
    runner = build(tmp_path, FakeOverpass())

    combos = runner.all_combos()

    assert len(combos) == 4
    # City-major: one city's categories are crawled together, so a partial
    # run gives usable coverage of somewhere rather than a thin smear.
    assert [(c.name, k.name) for c, k in combos] == [
        ("Москва", "Кафе"), ("Москва", "Аптеки"),
        ("Казань", "Кафе"), ("Казань", "Аптеки"),
    ]


@pytest.mark.asyncio
async def test_combos_per_run_bounds_the_number_of_overpass_queries(tmp_path):
    overpass = FakeOverpass()
    settings = make_settings(tmp_path, combos_per_run=3)
    runner = build(tmp_path, overpass, settings=settings)

    summary = await runner.run_once()

    # The whole point: a run is a bounded slice of the grid.
    assert len(overpass.calls) == 3
    assert summary["combos_processed"] == 3
    assert summary["grid_total"] == 4
    assert summary["remaining_combos"] == 1


@pytest.mark.asyncio
async def test_the_next_run_continues_where_the_previous_stopped(tmp_path):
    settings = make_settings(tmp_path, combos_per_run=3)

    first_overpass = FakeOverpass()
    await build(tmp_path, first_overpass, settings=settings).run_once()

    # A brand-new runner and a fresh checkpoint load, as a new process does.
    second_overpass = FakeOverpass()
    summary = await build(tmp_path, second_overpass, settings=settings).run_once()

    assert len(second_overpass.calls) == 1  # only the leftover pair
    assert summary["grid_done"] == 4
    # No pair was queried twice across the two runs.
    assert len(first_overpass.calls) + len(second_overpass.calls) == 4
    assert len(set(first_overpass.calls) | set(second_overpass.calls)) == 4


@pytest.mark.asyncio
async def test_a_completed_grid_starts_a_new_cycle_rather_than_idling(tmp_path):
    settings = make_settings(tmp_path, combos_per_run=50)
    await build(tmp_path, FakeOverpass(), settings=settings).run_once()  # completes all 4

    overpass = FakeOverpass()
    summary = await build(tmp_path, overpass, settings=settings).run_once()

    # A scheduled worker should keep refreshing, not go permanently quiet.
    assert len(overpass.calls) == 4
    assert summary["combos_processed"] == 4


@pytest.mark.asyncio
async def test_reset_progress_restarts_the_grid(tmp_path):
    settings = make_settings(tmp_path, combos_per_run=2)
    await build(tmp_path, FakeOverpass(), settings=settings).run_once()

    reset_settings = make_settings(tmp_path, combos_per_run=2, reset_progress=True)
    overpass = FakeOverpass()
    await build(tmp_path, overpass, settings=reset_settings).run_once()

    first_pair = (CITIES[0].as_tuple, "amenity", "cafe")
    assert overpass.calls[0] == first_pair  # started over from the top


@pytest.mark.asyncio
async def test_checkpoint_is_written_to_disk_during_the_run(tmp_path):
    settings = make_settings(tmp_path, combos_per_run=2)
    runner = build(tmp_path, FakeOverpass(), settings=settings)

    await runner.run_once()

    # Persisted per combo, so an interrupted run doesn't re-pay for queries.
    reloaded = load_progress(settings.progress_file)
    assert reloaded.done_count == 2
    assert reloaded.is_done("Москва", "Кафе")


@pytest.mark.asyncio
async def test_summary_names_the_pair_the_next_run_will_start_with(tmp_path):
    settings = make_settings(tmp_path, combos_per_run=1)
    runner = build(tmp_path, FakeOverpass(), settings=settings)

    summary = await runner.run_once()

    assert summary["next_run_starts_with"] == "Москва / Аптеки"


# --- data quality: chains, names, phones, sites --------------------------


@pytest.mark.asyncio
async def test_chain_branches_are_filtered_out(tmp_path):
    overpass = FakeOverpass(default_elements=[
        node(1, "Кафе Уют", phone="+7 495 111 11 11"),
        node(2, "Пятёрочка"),
        node(3, "Магнит у дома"),
        node(4, "Пекарня Хлебница"),
    ])
    settings = make_settings(tmp_path, combos_per_run=1)
    runner = build(tmp_path, overpass, settings=settings, stoplist=["Пятёрочка", "Магнит"])

    summary = await runner.run_once()

    assert summary["skipped_chains"] == 2
    assert summary["sent"] == 2  # Уют and Хлебница survive


@pytest.mark.asyncio
async def test_an_independent_business_is_not_filtered_by_a_chain_prefix(tmp_path):
    """Regression guard: substring matching would kill this real lead."""
    overpass = FakeOverpass(default_elements=[node(1, "Магнитогорская аптека")])
    settings = make_settings(tmp_path, combos_per_run=1)
    runner = build(tmp_path, overpass, settings=settings, stoplist=["Магнит"])

    summary = await runner.run_once()

    assert summary["skipped_chains"] == 0
    assert summary["sent"] == 1


@pytest.mark.asyncio
async def test_elements_without_a_name_are_counted_and_skipped(tmp_path):
    overpass = FakeOverpass(default_elements=[
        node(1, "Кафе Уют"),
        node(2, None),
        node(3, None),
    ])
    settings = make_settings(tmp_path, combos_per_run=1)
    runner = build(tmp_path, overpass, settings=settings)

    summary = await runner.run_once()

    assert summary["skipped_no_name"] == 2
    assert summary["sent"] == 1


@pytest.mark.asyncio
async def test_statistics_count_phones_and_missing_sites(tmp_path):
    overpass = FakeOverpass(default_elements=[
        node(1, "Кафе Уют", phone="+7 495 111 11 11"),                       # phone, no site
        node(2, "Пекарня", website="https://pekarnya.ru"),                   # site, no phone
        node(3, "Стоматология", phone="+7 495 222 22 22", website="x.ru"),   # both
        node(4, "Цветы"),                                                     # neither
    ])
    settings = make_settings(tmp_path, combos_per_run=1)
    runner = build(tmp_path, overpass, settings=settings)

    summary = await runner.run_once()

    assert summary["sent"] == 4
    assert summary["with_phone"] == 2
    assert summary["without_site"] == 2  # Уют and Цветы
    assert summary["elements_seen"] == 4


@pytest.mark.asyncio
async def test_the_same_element_across_two_categories_is_deduped(tmp_path):
    shared = [node(1, "Кафе Уют")]
    overpass = FakeOverpass(elements_by_key={
        ("amenity", "cafe"): shared,
        ("amenity", "pharmacy"): shared,
    })
    settings = make_settings(tmp_path, combos_per_run=2)
    runner = build(tmp_path, overpass, settings=settings)

    summary = await runner.run_once()

    assert summary["sent"] == 1
    assert summary["skipped_duplicates"] == 1


# --- target ceiling -------------------------------------------------------


@pytest.mark.asyncio
async def test_target_per_day_stops_the_run_early(tmp_path):
    overpass = FakeOverpass(default_elements=[node(i, f"Кафе {i}") for i in range(1, 11)])
    settings = make_settings(tmp_path, combos_per_run=4, target_per_day=5)
    runner = build(tmp_path, overpass, settings=settings)

    summary = await runner.run_once()

    assert summary["sent"] == 5
    # Unprocessed pairs stay pending for the next run rather than being
    # marked done without ever being queried.
    assert summary["remaining_combos"] > 0


@pytest.mark.asyncio
async def test_leads_actually_reach_the_core(tmp_path):
    overpass = FakeOverpass(default_elements=[node(1, "Кафе Уют", phone="+7 495 111 11 11")])
    settings = make_settings(tmp_path, combos_per_run=1)
    core = FakeCore()
    runner = OsmRunner(settings, CITIES, CATEGORIES, overpass, core,
                       ProgressTracker(settings.progress_file), ChainFilter([]))

    await runner.run_once()

    assert len(core.sent) == 1
    assert core.sent[0].name == "Кафе Уют"
    assert core.sent[0].city == "Москва"
    assert core.sent[0].source == "OSM"
