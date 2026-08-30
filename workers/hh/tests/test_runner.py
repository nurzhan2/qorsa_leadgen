"""End-to-end tests for HhRunner with a fake HH client and a fake core -
no network. This is where the pieces meet: filter -> group -> details ->
map -> send.
"""

import pytest

from workers.hh.config import Area, Settings
from workers.hh.filters import EmployerFilter
from workers.hh.hh_client import HhAuthError
from workers.hh.runner import HhRunner
from workers.hh.tests.fixtures import EMPLOYER_DETAIL, vacancy


class FakeHh:
    def __init__(self, vacancies_by_query=None, default=None, employers=None, auth_error=False):
        self._by_query = vacancies_by_query or {}
        self._default = default if default is not None else []
        self._employers = employers or {}
        self._auth_error = auth_error
        self.queries = []
        self.employer_calls = []
        self.requests_made = 0
        self.rate_limit_hits = 0

    async def iter_vacancies(self, text, area, max_pages):
        if self._auth_error:
            raise HhAuthError("403 - set HH_TOKEN")
        self.queries.append((text, area))
        self.requests_made += 1
        return list(self._by_query.get((text, area), self._default))

    async def fetch_employer(self, employer_id):
        self.employer_calls.append(employer_id)
        self.requests_made += 1
        return self._employers.get(employer_id)


class FakeCore:
    def __init__(self):
        self.sent = []

    async def send_leads(self, batch):
        self.sent.extend(batch)

    async def aclose(self):
        pass


AREAS = [Area(name="Москва", id="1")]
KEYWORDS = ["веб-разработчик"]


def settings(**over):
    base = dict(hh_token="t", target_per_day=1000, fetch_employer_details=False,
                stale_days=45, max_pages_per_query=1)
    base.update(over)
    return Settings(**base)


def build(hh, core=None, cfg=None, keywords=None, areas=None):
    cfg = cfg or settings()
    return HhRunner(cfg, keywords or KEYWORDS, areas or AREAS, hh, core or FakeCore(),
                    EmployerFilter(cfg.agencies, cfg.giants,
                                   cfg.skipped_employer_types, cfg.skip_accredited_it))


# --- the happy path -------------------------------------------------------


@pytest.mark.asyncio
async def test_collects_and_sends_a_lead():
    hh = FakeHh(default=[vacancy(employer_id="100", employer_name="ООО Ромашка")])
    core = FakeCore()

    summary = await build(hh, core).run_once()

    assert summary["vacancies_seen"] == 1
    assert summary["employers_found"] == 1
    assert summary["sent"] == 1
    assert len(core.sent) == 1
    assert core.sent[0].name == "ООО Ромашка"
    assert core.sent[0].source == "HH"


@pytest.mark.asyncio
async def test_queries_every_keyword_area_pair():
    hh = FakeHh(default=[])
    cfg = settings()
    runner = HhRunner(cfg, ["a", "b"], [Area(name="М", id="1"), Area(name="СПб", id="2")],
                      hh, FakeCore())

    await runner.run_once()

    assert set(hh.queries) == {("a", "1"), ("a", "2"), ("b", "1"), ("b", "2")}


# --- dedup by employer ----------------------------------------------------


@pytest.mark.asyncio
async def test_one_company_with_five_vacancies_is_sent_once():
    hh = FakeHh(default=[
        vacancy(vacancy_id=str(i), employer_id="100", employer_name="ООО Ромашка")
        for i in range(5)
    ])
    core = FakeCore()

    summary = await build(hh, core).run_once()

    assert summary["vacancies_seen"] == 5
    assert summary["employers_found"] == 1
    assert len(core.sent) == 1
    assert core.sent[0].raw["open_vacancies_matched"] == 5


@pytest.mark.asyncio
async def test_source_url_points_at_the_stalest_vacancy():
    hh = FakeHh(default=[
        vacancy(vacancy_id="fresh", employer_id="100", published_at="2026-08-25T10:00:00+0300"),
        vacancy(vacancy_id="rotten", employer_id="100", published_at="2025-01-10T10:00:00+0300"),
    ])
    core = FakeCore()

    await build(hh, core).run_once()

    assert core.sent[0].source_url == "https://hh.ru/vacancy/rotten"
    assert core.sent[0].raw["is_stale"] is True


# --- filtering ------------------------------------------------------------


@pytest.mark.asyncio
async def test_agencies_and_giants_are_filtered_out():
    hh = FakeHh(default=[
        vacancy(vacancy_id="1", employer_id="1", employer_name="ООО Ромашка"),
        vacancy(vacancy_id="2", employer_id="2", employer_name="Кадровое агентство Профи"),
        vacancy(vacancy_id="3", employer_id="3", employer_name="Сбербанк"),
    ])
    core = FakeCore()

    summary = await build(hh, core).run_once()

    assert summary["skipped_agency"] == 1
    assert summary["skipped_giant"] == 1
    assert summary["sent"] == 1
    assert core.sent[0].name == "ООО Ромашка"


@pytest.mark.asyncio
async def test_it_accredited_employers_are_filtered_from_the_search_result():
    """No extra request needed - the flag is on the search item itself."""
    hh = FakeHh(default=[
        vacancy(vacancy_id="1", employer_id="1", employer_name="ООО Ромашка"),
        vacancy(vacancy_id="2", employer_id="2", employer_name="ООО Технологии",
                accredited_it=True),
    ])

    summary = await build(hh).run_once()

    assert summary["skipped_accredited_it"] == 1
    assert summary["sent"] == 1


@pytest.mark.asyncio
async def test_agency_detected_only_via_hh_employer_type_is_dropped_late():
    """A neutral-sounding agency that the name list can't catch - HH's own
    classification does."""
    hh = FakeHh(
        default=[vacancy(employer_id="100", employer_name="Вектор Групп")],
        employers={"100": dict(EMPLOYER_DETAIL, id="100", name="Вектор Групп", type="agency")},
    )
    core = FakeCore()

    summary = await build(hh, core, cfg=settings(fetch_employer_details=True)).run_once()

    assert summary["skipped_employer_type"] == 1
    assert summary["sent"] == 0
    assert core.sent == []


# --- employer details -----------------------------------------------------


@pytest.mark.asyncio
async def test_employer_details_fill_in_the_domain():
    hh = FakeHh(
        default=[vacancy(employer_id="100", employer_name="ООО Ромашка")],
        employers={"100": dict(EMPLOYER_DETAIL, id="100", name="ООО Ромашка",
                               type="company", site_url="https://www.romashka.ru/")},
    )
    core = FakeCore()

    await build(hh, core, cfg=settings(fetch_employer_details=True)).run_once()

    assert core.sent[0].domain == "romashka.ru"
    assert core.sent[0].has_site is True
    assert hh.employer_calls == ["100"]


@pytest.mark.asyncio
async def test_details_are_fetched_once_per_employer_not_per_vacancy():
    """Filtering and grouping happen BEFORE details precisely so this second
    request stays cheap."""
    hh = FakeHh(
        default=[vacancy(vacancy_id=str(i), employer_id="100") for i in range(6)],
        employers={"100": dict(EMPLOYER_DETAIL, id="100", type="company")},
    )

    await build(hh, cfg=settings(fetch_employer_details=True)).run_once()

    assert hh.employer_calls == ["100"]


@pytest.mark.asyncio
async def test_details_can_be_switched_off_entirely():
    hh = FakeHh(default=[vacancy(employer_id="100")],
                employers={"100": EMPLOYER_DETAIL})
    core = FakeCore()

    await build(hh, core, cfg=settings(fetch_employer_details=False)).run_once()

    assert hh.employer_calls == []
    assert core.sent[0].domain is None
    assert core.sent[0].has_site is False


@pytest.mark.asyncio
async def test_detail_fetches_are_capped():
    hh = FakeHh(
        default=[vacancy(vacancy_id=str(i), employer_id=str(i)) for i in range(10)],
        employers={str(i): dict(EMPLOYER_DETAIL, id=str(i), type="company") for i in range(10)},
    )

    summary = await build(hh, cfg=settings(fetch_employer_details=True,
                                           max_employer_details=3)).run_once()

    assert summary["employer_details_fetched"] == 3
    assert summary["sent"] == 10  # the rest still ship, just without a domain


# --- limits and failure ---------------------------------------------------


@pytest.mark.asyncio
async def test_target_per_day_caps_the_run():
    hh = FakeHh(default=[vacancy(vacancy_id=str(i), employer_id=str(i)) for i in range(20)])
    core = FakeCore()

    summary = await build(hh, core, cfg=settings(target_per_day=5)).run_once()

    assert summary["sent"] == 5
    assert len(core.sent) == 5


@pytest.mark.asyncio
async def test_stalest_employers_are_sent_first_when_the_run_is_truncated():
    """If TARGET_PER_DAY cuts the batch, it must cut the COLDEST leads."""
    hh = FakeHh(default=[
        vacancy(vacancy_id="fresh", employer_id="1", published_at="2026-08-29T10:00:00+0300"),
        vacancy(vacancy_id="old", employer_id="2", published_at="2024-01-01T10:00:00+0300"),
    ])
    core = FakeCore()

    await build(hh, core, cfg=settings(target_per_day=1)).run_once()

    assert len(core.sent) == 1
    assert core.sent[0].raw["employer_id"] == "2"


@pytest.mark.asyncio
async def test_an_auth_error_aborts_the_run_instead_of_grinding_through_the_grid():
    hh = FakeHh(auth_error=True)
    core = FakeCore()
    cfg = settings()
    runner = HhRunner(cfg, ["a", "b", "c"], [Area(name="М", id="1"), Area(name="СПб", id="2")],
                      hh, core)

    summary = await runner.run_once()

    assert summary["aborted"] is not None
    assert "HH_TOKEN" in summary["aborted"]
    assert summary["sent"] == 0
    assert hh.queries == []


@pytest.mark.asyncio
async def test_vacancies_without_an_employer_id_are_counted_and_skipped():
    anonymous = vacancy(vacancy_id="1")
    anonymous["employer"] = {"name": "Аноним"}
    hh = FakeHh(default=[anonymous])

    summary = await build(hh).run_once()

    assert summary["skipped_no_employer"] == 1
    assert summary["sent"] == 0


@pytest.mark.asyncio
async def test_statistics_report_stale_and_salary_counts():
    hh = FakeHh(default=[
        vacancy(vacancy_id="1", employer_id="1", published_at="2024-01-01T10:00:00+0300",
                salary={"from": 100000, "to": None, "currency": "RUR"}),
        vacancy(vacancy_id="2", employer_id="2", published_at="2026-08-29T10:00:00+0300",
                salary=None),
    ])

    summary = await build(hh).run_once()

    assert summary["sent"] == 2
    assert summary["stale_employers"] == 1
    assert summary["with_salary"] == 1
    assert summary["stale_threshold_days"] == 45
