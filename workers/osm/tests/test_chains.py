"""Unit tests for chains.py - the chain/franchise stop-list. Pure string
logic, no network.

The tests that matter most are the FALSE-POSITIVE ones: over-filtering is a
silent failure. A dropped chain is invisible in the output, and so is a
dropped independent business - but the second one is a lost lead.
"""

import pytest

from workers.osm.chains import ChainFilter, normalize_name
from workers.osm.config import Settings


@pytest.fixture
def chains() -> ChainFilter:
    return ChainFilter([
        "Пятёрочка", "Магнит", "Сбербанк", "Ozon", "McDonalds",
        "Красное и Белое", "H&M", "М.Видео", "Fix Price",
    ])


# --- normalize_name -------------------------------------------------------


def test_normalize_collapses_case_and_whitespace():
    assert normalize_name("  ПЯТЁРОЧКА   Дом  ") == "пятерочка дом"


def test_normalize_unifies_yo_and_ye():
    # Russian business names are written both ways, interchangeably.
    assert normalize_name("Пятёрочка") == normalize_name("Пятерочка")


def test_normalize_unifies_dash_variants():
    assert normalize_name("Альфа–Банк") == normalize_name("Альфа-Банк")


def test_normalize_handles_none_and_blank():
    assert normalize_name(None) == ""
    assert normalize_name("   ") == ""


# --- chains are filtered --------------------------------------------------


@pytest.mark.parametrize("name", [
    "Пятёрочка",
    "Пятерочка",                 # ё/е variant
    "ПЯТЁРОЧКА",                 # case
    "Магазин Пятёрочка №123",    # embedded
    "Магнит",
    "Сбербанк",
    "Ozon",
    "OZON",
    "McDonalds",
])
def test_known_chains_are_filtered(chains: ChainFilter, name):
    assert chains.is_chain(name) is True


def test_multiword_chain_matches_regardless_of_spacing(chains: ChainFilter):
    assert chains.is_chain("Красное и Белое") is True
    assert chains.is_chain("Красное   и   Белое") is True


def test_chain_with_punctuation_matches(chains: ChainFilter):
    assert chains.is_chain("H&M") is True
    assert chains.is_chain("М.Видео") is True
    assert chains.is_chain("Fix Price") is True


def test_matched_chain_reports_which_entry_fired(chains: ChainFilter):
    # Returning the needle (not just True) is what makes the "why was this
    # dropped" log usable when tuning the list.
    assert chains.matched_chain("Магазин Пятёрочка №5") == "Пятёрочка"
    assert chains.matched_chain("Кафе Уют") is None


# --- independents are NOT filtered (the important half) -------------------


@pytest.mark.parametrize("name", [
    "Магнитогорская аптека",     # "Магнит" is a PREFIX, not a word
    "Магнитка",
    "Кафе у метро",              # "Метро" deliberately absent from the list
    "Салон красоты Верный стиль",
    "Пекарня Хлебница",
    "Автосервис на Ленина",
    "Стоматология Улыбка",
    "ООО Озонотерапия",          # contains "озон" but is not Ozon
])
def test_independent_businesses_are_not_filtered(chains: ChainFilter, name):
    assert chains.is_chain(name) is False, f"over-filtered a real lead: {name}"


def test_word_boundary_is_the_whole_point():
    """A raw substring match would kill all of these. This is the regression
    guard for the filter getting "simplified" back to `needle in name`."""
    filt = ChainFilter(["Магнит", "Лента", "Верный"])

    assert filt.is_chain("Магнитогорская аптека") is False
    assert filt.is_chain("Лентяйка") is False
    assert filt.is_chain("Проверенный мастер") is False
    # ...while the actual chains still match.
    assert filt.is_chain("Магнит у дома") is True
    assert filt.is_chain("Гипермаркет Лента") is True


def test_blank_and_missing_names_are_not_chains(chains: ChainFilter):
    assert chains.is_chain(None) is False
    assert chains.is_chain("") is False
    assert chains.is_chain("   ") is False


def test_empty_stoplist_filters_nothing():
    filt = ChainFilter([])

    assert len(filt) == 0
    assert filt.is_chain("Пятёрочка") is False


def test_blank_entries_in_the_stoplist_are_ignored():
    """A trailing comma in CHAIN_STOPLIST must not produce an empty needle
    that matches everything."""
    filt = ChainFilter(["Магнит", "", "   "])

    assert len(filt) == 1
    assert filt.is_chain("Кафе Уют") is False


# --- the shipped default list -------------------------------------------


def test_shipped_stoplist_parses_and_has_no_dangerous_needles():
    settings = Settings()
    needles = settings.stoplist

    assert len(needles) > 40
    # A one- or two-character needle would match enormous numbers of names.
    too_short = [n for n in needles if len(n) < 3]
    assert too_short == [], f"dangerously short stop-list entries: {too_short}"
    # Purely numeric needles ("36", "6") match any name containing a digit.
    numeric = [n for n in needles if n.replace(".", "").isdigit()]
    assert numeric == [], f"numeric stop-list entries would over-match: {numeric}"


def test_shipped_stoplist_does_not_filter_ordinary_businesses():
    filt = ChainFilter(Settings().stoplist)

    for name in [
        "Кафе Уют", "Стоматология Дента", "Автосервис Гараж 47",
        "Пекарня у дома", "Магнитогорская аптека", "Цветы 24 часа",
        "Салон красоты Ирина", "Шиномонтаж на Мира",
    ]:
        assert filt.is_chain(name) is False, f"shipped list over-filters: {name}"


def test_shipped_stoplist_catches_the_obvious_chains():
    filt = ChainFilter(Settings().stoplist)

    for name in ["Пятёрочка", "Магнит", "Сбербанк", "Wildberries", "KFC", "DNS", "Леруа Мерлен"]:
        assert filt.is_chain(name) is True, f"shipped list misses a chain: {name}"
