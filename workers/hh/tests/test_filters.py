"""Unit tests for filters.py - who is NOT a lead.

As in workers/osm, the FALSE-POSITIVE tests carry the weight: dropping a
chain is visible in the counters, dropping a real prospect is silent.
"""

import pytest

from workers.hh.config import Settings
from workers.hh.filters import EmployerFilter, NameFilter, normalize_name
from workers.hh.tests.fixtures import EMPLOYER_TYPES


@pytest.fixture
def employer_filter() -> EmployerFilter:
    return EmployerFilter(
        agencies=["кадровое агентство", "рекрутинг", "подбор персонала", "ancor"],
        giants=["сбербанк", "яндекс", "ozon", "vk", "mail.ru"],
        skip_types=["agency", "private_recruiter"],
        skip_accredited_it=True,
    )


# --- normalize_name -------------------------------------------------------


def test_normalize_collapses_case_and_whitespace():
    assert normalize_name("  ООО   Ромашка  ") == "ооо ромашка"


def test_normalize_unifies_yo_and_dashes():
    assert normalize_name("Алёна-Дизайн") == normalize_name("Алена–Дизайн")


def test_normalize_handles_none():
    assert normalize_name(None) == ""


# --- agencies and giants are filtered ------------------------------------


@pytest.mark.parametrize("name", [
    "Кадровое агентство Успех",
    "КАДРОВОЕ АГЕНТСТВО",
    "Рекрутинг Плюс",
    "Подбор персонала для вас",
    "ANCOR",
])
def test_recruiting_agencies_are_filtered(employer_filter, name):
    reason = employer_filter.skip_reason(name=name)

    assert reason is not None
    assert reason[0] == "agency_name"


@pytest.mark.parametrize("name", ["Сбербанк", "Яндекс", "Ozon", "VK", "Mail.ru"])
def test_it_giants_are_filtered(employer_filter, name):
    reason = employer_filter.skip_reason(name=name)

    assert reason is not None
    assert reason[0] == "giant_name"


# --- real prospects survive (the half that matters) ----------------------


@pytest.mark.parametrize("name", [
    "ООО Ромашка",
    "Студия дизайна интерьера Уют",
    "Кадрырезерв Дизайн",        # "кадры" is a PREFIX here, not a word
    "Мебельная фабрика Дуб",
    "Архитектурное бюро Форма",
    "Ландшафт Плюс",
    "Стоматология Улыбка",
    "ВКонтакте Строй",           # contains "вк" only as part of a word
])
def test_real_prospects_are_not_filtered(employer_filter, name):
    assert employer_filter.skip_reason(name=name) is None, f"over-filtered: {name}"


def test_word_boundary_is_the_point():
    """Regression guard for the filter being "simplified" to `needle in name`."""
    filt = NameFilter(["кадры", "vk", "лента"], "test")

    assert filt.matched("Кадрырезерв") is None
    assert filt.matched("Вкусно и точка") is None
    assert filt.matched("Лентяйка") is None
    # ...while the real names still match.
    assert filt.matched("Кадры Профи") == "кадры"
    assert filt.matched("VK Group") == "vk"


def test_blank_names_are_not_filtered(employer_filter):
    assert employer_filter.skip_reason(name=None) is None
    assert employer_filter.skip_reason(name="") is None


def test_blank_stoplist_entries_do_not_match_everything():
    """A trailing comma in the .env list must not produce an empty needle."""
    filt = NameFilter(["сбербанк", "", "   "], "test")

    assert len(filt) == 1
    assert filt.matched("ООО Ромашка") is None


# --- HH's own signals beat name guessing ---------------------------------


def test_hh_employer_type_agency_is_filtered(employer_filter):
    """HH classifies agencies itself - more reliable than any name list.
    These ids are the live values from /dictionaries."""
    reason = employer_filter.skip_reason(name="Совершенно Обычное ООО", employer_type="agency")

    assert reason == ("employer_type", "agency")


def test_hh_employer_type_private_recruiter_is_filtered(employer_filter):
    reason = employer_filter.skip_reason(name="Иванов И.И.", employer_type="private_recruiter")

    assert reason == ("employer_type", "private_recruiter")


def test_ordinary_company_type_is_kept(employer_filter):
    assert employer_filter.skip_reason(name="ООО Ромашка", employer_type="company") is None


def test_every_dictionary_type_is_classified_deliberately(employer_filter):
    """All six live employer_type values, so a new one can't sneak through
    unnoticed."""
    skipped = {t for t in EMPLOYER_TYPES
               if employer_filter.skip_reason(name="Нейтральное Имя", employer_type=t)}

    assert skipped == {"agency", "private_recruiter"}


def test_it_accredited_companies_are_filtered(employer_filter):
    """HH's accreditation flag means in-house developers - not a customer
    for outsourced web work."""
    reason = employer_filter.skip_reason(name="ООО Ромашка", accredited_it=True)

    assert reason == ("accredited_it", "hh_it_accreditation")


def test_accredited_filter_can_be_switched_off():
    filt = EmployerFilter(agencies=[], giants=[], skip_types=[], skip_accredited_it=False)

    assert filt.skip_reason(name="ООО Ромашка", accredited_it=True) is None


def test_hh_signals_are_reported_before_name_guesses(employer_filter):
    """When both fire, the reason should be the fact from HH, not our
    inference - it makes the counters honest."""
    reason = employer_filter.skip_reason(name="Кадровое агентство Успех", employer_type="agency")

    assert reason[0] == "employer_type"


# --- the shipped default lists -------------------------------------------


def test_shipped_lists_parse_and_have_no_dangerous_needles():
    settings = Settings(hh_token="")
    needles = settings.agencies + settings.giants

    assert len(settings.agencies) > 10
    assert len(settings.giants) > 30
    too_short = [n for n in needles if len(n) < 2]
    assert too_short == [], f"dangerously short entries: {too_short}"


def test_shipped_lists_catch_the_obvious_and_spare_the_rest():
    settings = Settings(hh_token="")
    filt = EmployerFilter(settings.agencies, settings.giants,
                          settings.skipped_employer_types, settings.skip_accredited_it)

    for name in ["Сбербанк", "Яндекс", "Wildberries", "Кадровое агентство Профи", "EPAM"]:
        assert filt.skip_reason(name=name) is not None, f"missed: {name}"

    for name in ["ООО Ромашка", "Дизайн-студия Интерьер", "Автосервис на Мира",
                 "Пекарня Хлебница", "Архитектурная мастерская"]:
        assert filt.skip_reason(name=name) is None, f"over-filtered: {name}"
