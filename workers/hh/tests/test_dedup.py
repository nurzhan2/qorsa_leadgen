"""Unit tests for dedup.py: one lead per employer, represented by its
STALEST vacancy. Pure, no network."""

from workers.hh.dedup import EmployerAggregator
from workers.hh.tests.fixtures import vacancy


def add(agg, v, age, stale=False, salary=False, keyword="веб-разработчик"):
    agg.add(v, age=age, stale=stale, has_salary=salary, keyword=keyword)


# --- grouping -------------------------------------------------------------


def test_five_vacancies_from_one_company_become_one_lead():
    """The core reason this module exists: a company hiring a frontend dev,
    a backend dev and a designer is ONE company."""
    agg = EmployerAggregator()
    for i in range(5):
        add(agg, vacancy(vacancy_id=str(i), employer_id="100"), age=10)

    buckets = agg.buckets()

    assert len(agg) == 1
    assert len(buckets) == 1
    assert buckets[0].vacancy_count == 5


def test_different_employers_stay_separate():
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="1", employer_id="100", employer_name="А"), age=5)
    add(agg, vacancy(vacancy_id="2", employer_id="200", employer_name="Б"), age=5)

    assert len(agg) == 2


def test_vacancies_without_an_employer_id_are_dropped():
    """Nothing to group on, and an anonymous posting has no company to sell
    to anyway."""
    agg = EmployerAggregator()
    anonymous = vacancy(vacancy_id="1")
    anonymous["employer"] = {"name": "Аноним"}

    add(agg, anonymous, age=10)

    assert len(agg) == 0
    assert agg.skipped_no_employer == 1


def test_missing_employer_block_entirely_is_handled():
    agg = EmployerAggregator()
    broken = vacancy(vacancy_id="1")
    del broken["employer"]

    add(agg, broken, age=10)

    assert len(agg) == 0
    assert agg.skipped_no_employer == 1


# --- which vacancy represents the employer -------------------------------


def test_the_stalest_vacancy_wins_as_representative():
    """sourceUrl must point at the vacancy that best evidences "can't
    hire" - that's the one a salesperson should open."""
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="fresh", employer_id="100"), age=3)
    add(agg, vacancy(vacancy_id="rotten", employer_id="100"), age=120)
    add(agg, vacancy(vacancy_id="middling", employer_id="100"), age=40)

    bucket = agg.buckets()[0]

    assert bucket.best["id"] == "rotten"
    assert bucket.best_age == 120
    assert bucket.best["alternate_url"] == "https://hh.ru/vacancy/rotten"


def test_stalest_wins_regardless_of_insertion_order():
    for order in ([120, 3], [3, 120]):
        agg = EmployerAggregator()
        for i, age in enumerate(order):
            add(agg, vacancy(vacancy_id=f"v{age}", employer_id="100"), age=age)
        assert agg.buckets()[0].best_age == 120


def test_a_dated_vacancy_beats_an_undated_one():
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="undated", employer_id="100"), age=None)
    add(agg, vacancy(vacancy_id="dated", employer_id="100"), age=7)

    assert agg.buckets()[0].best["id"] == "dated"


def test_ties_keep_the_first_seen_for_determinism():
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="first", employer_id="100"), age=30)
    add(agg, vacancy(vacancy_id="second", employer_id="100"), age=30)

    assert agg.buckets()[0].best["id"] == "first"


# --- flags roll up across the employer's vacancies -----------------------


def test_any_stale_vacancy_marks_the_employer_stale():
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="1", employer_id="100"), age=3, stale=False)
    add(agg, vacancy(vacancy_id="2", employer_id="100"), age=90, stale=True)

    assert agg.buckets()[0].has_stale is True


def test_any_salary_marks_the_employer_as_having_a_budget():
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="1", employer_id="100"), age=5, salary=False)
    add(agg, vacancy(vacancy_id="2", employer_id="100"), age=5, salary=True)

    assert agg.buckets()[0].has_salary is True


def test_keywords_accumulate_without_duplicates():
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="1", employer_id="100"), age=5, keyword="веб-дизайнер")
    add(agg, vacancy(vacancy_id="2", employer_id="100"), age=5, keyword="таргетолог")
    add(agg, vacancy(vacancy_id="3", employer_id="100"), age=5, keyword="веб-дизайнер")

    assert agg.buckets()[0].keywords == ["веб-дизайнер", "таргетолог"]


# --- ordering -------------------------------------------------------------


def test_buckets_come_back_stalest_first():
    """TARGET_PER_DAY truncates the run, so the hottest leads must lead."""
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="a", employer_id="1"), age=5)
    add(agg, vacancy(vacancy_id="b", employer_id="2"), age=200)
    add(agg, vacancy(vacancy_id="c", employer_id="3"), age=60)

    assert [b.employer_id for b in agg.buckets()] == ["2", "3", "1"]


def test_undated_employers_sort_last():
    agg = EmployerAggregator()
    add(agg, vacancy(vacancy_id="a", employer_id="1"), age=None)
    add(agg, vacancy(vacancy_id="b", employer_id="2"), age=1)

    assert [b.employer_id for b in agg.buckets()] == ["2", "1"]
