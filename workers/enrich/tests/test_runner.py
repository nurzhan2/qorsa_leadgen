"""Tests for EnrichRunner with a fake core and a fake enricher - the
contract with the core (always PATCH, never loop) and the concurrency /
pacing limits."""

import asyncio

import pytest

from workers.enrich.config import Settings
from workers.enrich.core_client import CoreUnavailable, PendingCompany
from workers.enrich.enricher import FAILED, FOUND, NOTHING_FOUND, UNAVAILABLE, EnrichOutcome
from workers.enrich.runner import EnrichRunner
from workers.enrich.tests.support import NoSleep


def settings(**overrides) -> Settings:
    base = dict(enrich_batch=50, enrich_concurrency=5, request_delay_seconds=2.0, max_batches_per_run=10)
    base.update(overrides)
    return Settings(_env_file=None, **base)


def companies(*ids: str) -> list[PendingCompany]:
    return [PendingCompany(id=cid, name=f"Компания {cid}", domain=f"{cid}.kz") for cid in ids]


class FakeCore:
    def __init__(self, batches=None, repeat=None, down=False, patch_ok=True):
        self._batches = list(batches or [])
        self._repeat = repeat
        self._down = down
        self._patch_ok = patch_ok
        self.fetch_calls = 0
        self.patches: list = []

    async def fetch_pending(self, limit):
        self.fetch_calls += 1
        if self._down:
            raise CoreUnavailable("core unreachable: ConnectError")
        if self._repeat is not None:
            return list(self._repeat)
        return self._batches.pop(0) if self._batches else []

    async def patch_contacts(self, company_id, patch):
        self.patches.append((company_id, patch))
        return self._patch_ok


class FakeEnricher:
    def __init__(self, outcomes=None, delay=0.0, crash_for=()):
        self._outcomes = outcomes or {}
        self._delay = delay
        self._crash_for = set(crash_for)
        self.in_flight = 0
        self.max_in_flight = 0
        self.seen: list[str] = []

    async def enrich(self, company):
        self.seen.append(company.id)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            if company.id in self._crash_for:
                raise RuntimeError("parser exploded")
            return self._outcomes.get(company.id) or EnrichOutcome(
                NOTHING_FOUND, notes="контакты не найдены (страниц просмотрено: 1)", pages_fetched=1)
        finally:
            self.in_flight -= 1


def run(core, enricher, sleep=None, **setting_overrides):
    return EnrichRunner(settings(**setting_overrides), core, enricher, sleep=sleep or NoSleep()).run_once()


# --- always PATCH -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_company_where_nothing_was_found_is_still_patched():
    core = FakeCore(batches=[companies("a")])

    summary = await run(core, FakeEnricher())

    assert [cid for cid, _ in core.patches] == ["a"]
    body = core.patches[0][1].model_dump(by_alias=True, exclude_none=True)
    assert body == {"enrichNotes": "контакты не найдены (страниц просмотрено: 1)"}
    assert summary[NOTHING_FOUND] == 1


@pytest.mark.asyncio
async def test_an_unavailable_site_is_still_patched():
    core = FakeCore(batches=[companies("a")])
    enricher = FakeEnricher({"a": EnrichOutcome(UNAVAILABLE, notes="сайт недоступен: ConnectError")})

    summary = await run(core, enricher)

    assert core.patches[0][1].enrich_notes == "сайт недоступен: ConnectError"
    assert summary[UNAVAILABLE] == 1


@pytest.mark.asyncio
async def test_found_contacts_are_sent_in_the_core_contract_shape():
    core = FakeCore(batches=[companies("a")])
    enricher = FakeEnricher({"a": EnrichOutcome(FOUND, email="info@a.kz", phone="+77273551020",
                                                messenger="https://wa.me/77012345678",
                                                notes="email с /kontakty/")})

    summary = await run(core, enricher)

    assert core.patches[0][1].model_dump(by_alias=True, exclude_none=True) == {
        "email": "info@a.kz", "phone": "+77273551020", "messenger": "https://wa.me/77012345678",
        "enrichNotes": "email с /kontakty/"}
    assert summary["with_email"] == summary["with_phone"] == summary["with_messenger"] == 1


@pytest.mark.asyncio
async def test_a_crash_on_one_site_is_contained_and_still_recorded():
    core = FakeCore(batches=[companies("a", "b")])

    summary = await run(core, FakeEnricher(crash_for={"a"}))

    assert {cid for cid, _ in core.patches} == {"a", "b"}
    crashed = dict(core.patches)["a"]
    assert crashed.enrich_notes == "ошибка воркера: RuntimeError"
    assert summary[FAILED] == 1
    assert summary[NOTHING_FOUND] == 1


@pytest.mark.asyncio
async def test_a_failed_patch_is_counted():
    summary = await run(FakeCore(batches=[companies("a")], patch_ok=False), FakeEnricher())

    assert summary["patch_failed"] == 1
    assert summary["patched"] == 0


# --- never loop, drain the queue ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_core_that_keeps_returning_the_same_companies_does_not_cause_a_loop():
    # What happens when PATCHes fail: the core still lists them as pending.
    core = FakeCore(repeat=companies("a", "b"))
    enricher = FakeEnricher()

    summary = await run(core, enricher, enrich_batch=2)

    assert sorted(enricher.seen) == ["a", "b"]
    assert core.fetch_calls == 2
    assert summary["companies"] == 2


@pytest.mark.asyncio
async def test_drains_several_batches_until_a_short_one():
    core = FakeCore(batches=[companies("a", "b"), companies("c", "d"), companies("e")])
    enricher = FakeEnricher()

    summary = await run(core, enricher, enrich_batch=2)

    assert sorted(enricher.seen) == ["a", "b", "c", "d", "e"]
    assert core.fetch_calls == 3
    assert summary["batches"] == 3


@pytest.mark.asyncio
async def test_max_batches_per_run_is_respected():
    core = FakeCore(batches=[companies("a"), companies("b"), companies("c")])
    enricher = FakeEnricher()

    await run(core, enricher, enrich_batch=1, max_batches_per_run=2)

    assert enricher.seen == ["a", "b"]


@pytest.mark.asyncio
async def test_an_empty_queue_is_a_quiet_run():
    core = FakeCore(batches=[])
    summary = await run(core, FakeEnricher())

    assert summary["companies"] == 0
    assert summary["aborted"] is None


@pytest.mark.asyncio
async def test_a_core_that_is_down_aborts_the_run_cleanly():
    core = FakeCore(down=True)
    enricher = FakeEnricher()

    summary = await run(core, enricher)

    assert "core unreachable" in summary["aborted"]
    assert enricher.seen == []


# --- concurrency and pacing ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_is_bounded_by_the_semaphore():
    ids = [f"c{i}" for i in range(12)]
    enricher = FakeEnricher(delay=0.01)

    await run(FakeCore(batches=[companies(*ids)]), enricher, enrich_concurrency=3)

    assert enricher.max_in_flight == 3  # parallel - but never more than 3 sites at once
    assert len(enricher.seen) == 12


@pytest.mark.asyncio
async def test_there_is_a_pause_after_every_site():
    sleep = NoSleep()

    await run(FakeCore(batches=[companies("a", "b", "c")]), FakeEnricher(), sleep=sleep,
              request_delay_seconds=2.5)

    assert sleep.calls == [2.5, 2.5, 2.5]


def test_defaults_match_the_documented_env():
    s = Settings(_env_file=None)
    assert s.core_url == "http://localhost:8081"
    assert (s.enrich_batch, s.enrich_concurrency, s.request_delay_seconds) == (50, 5, 2.0)
    assert s.respect_robots is True
    assert s.run_once is True
    assert s.request_timeout_seconds == 15.0
    assert s.user_agent.startswith("qorsa-leadgen-enrich/")
