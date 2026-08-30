"""Unit tests for mapper.py: aggregated employer -> RawCompanyRequest.
Runs on HH's own published payload shape (see fixtures.py provenance)."""

from workers.hh.dedup import EmployerAggregator
from workers.hh.mapper import has_salary, map_employer_to_lead, normalize_domain, salary_summary
from workers.hh.tests.fixtures import EMPLOYER_DETAIL, VACANCY_ITEM, vacancy


def bucket_for(v, age=60, stale=True, salary=False, keyword="веб-разработчик"):
    agg = EmployerAggregator()
    agg.add(v, age=age, stale=stale, has_salary=salary, keyword=keyword)
    return agg.buckets()[0]


# --- normalize_domain -----------------------------------------------------


def test_normalize_domain_strips_scheme_www_and_path():
    assert normalize_domain("https://www.Romashka.RU/about?x=1#top") == "romashka.ru"


def test_normalize_domain_on_blank_is_none():
    for value in (None, "", "   "):
        assert normalize_domain(value) is None


# --- salary ---------------------------------------------------------------


def test_salary_from_hh_real_payload_is_detected():
    """HH's published example has salary.from = 100000, to = null."""
    assert has_salary(VACANCY_ITEM) is True
    assert salary_summary(VACANCY_ITEM)["from"] == 100000
    assert salary_summary(VACANCY_ITEM)["currency"] == "RUR"


def test_null_salary_is_no_salary():
    assert has_salary(vacancy(salary=None)) is False
    assert salary_summary(vacancy(salary=None)) is None


def test_salary_object_with_both_bounds_null_is_no_salary():
    """HH sends this shape; treating it as a budget signal would be a lie."""
    empty = {"from": None, "to": None, "currency": "RUR", "gross": None}

    assert has_salary(vacancy(salary=empty)) is False


def test_only_upper_bound_still_counts():
    assert has_salary(vacancy(salary={"from": None, "to": 200000, "currency": "RUR"})) is True


def test_salary_range_field_is_also_accepted():
    v = vacancy(salary=None)
    v["salary_range"] = {"from": 150000, "to": None, "currency": "RUR", "gross": False}

    assert has_salary(v) is True


# --- mapping --------------------------------------------------------------


def test_maps_a_real_hh_vacancy_to_a_lead():
    lead = map_employer_to_lead(bucket_for(VACANCY_ITEM, age=120, stale=True, salary=True),
                                stale_days=45, employer_details=EMPLOYER_DETAIL)

    assert lead is not None
    assert lead.name == "HeadHunter"
    assert lead.city == "Москва"
    assert lead.source == "HH"
    assert lead.source_url == "https://hh.ru/vacancy/7760476"
    assert lead.domain == "hh.ru"
    assert lead.has_site is True


def test_raw_carries_the_stale_detector_fields():
    lead = map_employer_to_lead(bucket_for(vacancy(published_at="2026-01-01T00:00:00+0300"),
                                           age=200, stale=True),
                                stale_days=45)

    assert lead.raw["age_days"] == 200
    assert lead.raw["is_stale"] is True
    assert lead.raw["stale_threshold_days"] == 45
    assert lead.raw["published_at"] == "2026-01-01T00:00:00+0300"
    assert lead.raw["vacancy_name"] == "Веб-разработчик"
    assert lead.raw["vacancy_url"] == "https://hh.ru/vacancy/1"
    assert lead.raw["employer_id"] == "100"


def test_salary_sets_budget_mentioned_for_the_core_scorer():
    """The core's ScoringService reads raw.budgetMentioned directly (+20)."""
    with_salary = map_employer_to_lead(
        bucket_for(vacancy(salary={"from": 120000, "to": None, "currency": "RUR"}), salary=True),
        stale_days=45)
    without = map_employer_to_lead(bucket_for(vacancy(salary=None), salary=False), stale_days=45)

    assert with_salary.raw["budgetMentioned"] is True
    assert without.raw["budgetMentioned"] is False


def test_no_employer_details_means_no_domain_and_has_site_false():
    lead = map_employer_to_lead(bucket_for(vacancy()), stale_days=45, employer_details=None)

    assert lead.domain is None
    assert lead.has_site is False


def test_employer_without_a_site_url_gets_has_site_false():
    details = dict(EMPLOYER_DETAIL, site_url=None)

    lead = map_employer_to_lead(bucket_for(vacancy()), stale_days=45, employer_details=details)

    assert lead.has_site is False


def test_employer_detail_name_wins_over_the_search_result_name():
    """The employer endpoint carries the canonical legal name."""
    details = dict(EMPLOYER_DETAIL, name='ООО "Ромашка Групп"')

    lead = map_employer_to_lead(bucket_for(vacancy(employer_name="Ромашка")),
                                stale_days=45, employer_details=details)

    assert lead.name == 'ООО "Ромашка Групп"'


def test_a_nameless_employer_produces_no_lead():
    v = vacancy(employer_name="")
    v["employer"]["name"] = ""

    assert map_employer_to_lead(bucket_for(v), stale_days=45) is None


def test_multiple_vacancies_are_reported_in_raw():
    agg = EmployerAggregator()
    agg.add(vacancy(vacancy_id="1", employer_id="100"), age=10, stale=False,
            has_salary=False, keyword="веб-дизайнер")
    agg.add(vacancy(vacancy_id="2", employer_id="100"), age=90, stale=True,
            has_salary=True, keyword="таргетолог")

    lead = map_employer_to_lead(agg.buckets()[0], stale_days=45)

    assert lead.raw["open_vacancies_matched"] == 2
    assert lead.raw["is_stale"] is True
    assert lead.raw["age_days"] == 90
    assert lead.raw["keywords"] == ["веб-дизайнер", "таргетолог"]
    # sourceUrl points at the stalest vacancy - the one worth opening.
    assert lead.source_url == "https://hh.ru/vacancy/2"


def test_employer_type_and_accreditation_ride_along_in_raw():
    v = vacancy(accredited_it=True)

    lead = map_employer_to_lead(bucket_for(v), stale_days=45, employer_details=EMPLOYER_DETAIL)

    assert lead.raw["employer_type"] == "company"
    assert lead.raw["accredited_it_employer"] is True


def test_lead_serializes_with_the_cores_camelcase_contract():
    lead = map_employer_to_lead(bucket_for(VACANCY_ITEM, salary=True),
                                stale_days=45, employer_details=EMPLOYER_DETAIL)
    payload = lead.model_dump(by_alias=True, exclude_none=True)

    assert payload["sourceUrl"] == "https://hh.ru/vacancy/7760476"
    assert payload["hasSite"] is True
    assert payload["source"] == "HH"
    assert payload["raw"]["budgetMentioned"] is True
