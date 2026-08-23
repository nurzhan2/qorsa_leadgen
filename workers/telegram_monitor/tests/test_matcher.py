"""Unit tests for matcher.py - plain string fixtures, no network, no
Telegram, no filesystem (the KeywordsConfig is built in-memory)."""

import pytest

from workers.telegram_monitor.config import KeywordCategory, KeywordsConfig
from workers.telegram_monitor.matcher import Matcher


@pytest.fixture
def keywords() -> KeywordsConfig:
    return KeywordsConfig(
        intent=KeywordCategory(
            weight=3,
            keywords=["нужен сайт", "ищу разработчика", "требуется бот", "нужен телеграм бот"],
        ),
        domain=KeywordCategory(
            weight=2,
            keywords=["сайт", "лендинг", "бот", "crm"],
        ),
        budget=KeywordCategory(
            weight=1,
            keywords=["бюджет", "оплата", "тз", "прайс", "стоимость"],
        ),
        anti_hiring=["в штат", "оклад", "график работы", "трудоустройство"],
        order_signals=["разовая задача", "бюджет", "по тз", "нужен фрилансер"],
    )


@pytest.fixture
def matcher(keywords: KeywordsConfig) -> Matcher:
    return Matcher(keywords)


def test_direct_intent_phrase_matches(matcher: Matcher):
    result = matcher.match("Всем привет! Нужен сайт для стоматологии, кто может помочь?")

    assert result.matched is True
    assert result.is_order is True
    assert result.rejected_reason is None
    assert result.category == "intent"
    assert result.budget_flag is False
    # intent hit "нужен сайт" (weight 3) + domain hit "сайт" (weight 2), both
    # legitimately present as whole words.
    assert result.weight == 5
    assert "нужен сайт" in result.matched_keywords["intent"]
    assert "сайт" in result.matched_keywords["domain"]


def test_intent_case_and_whitespace_insensitive(matcher: Matcher):
    result = matcher.match("  ИЩУ\n\nРАЗРАБОТЧИКА   для проекта  ")

    assert result.matched is True
    assert result.category == "intent"
    assert result.weight == 3
    # Regression guard: "бот" must NOT match inside "разработчика" - keyword
    # matching uses whole-word boundaries, not raw substring containment.
    assert "domain" not in result.matched_keywords


def test_pure_chatter_does_not_match(matcher: Matcher):
    result = matcher.match("Какой красивый сайт у вас получился, залипла на час!")

    assert result.matched is False
    assert result.is_order is False
    assert result.rejected_reason is None
    assert result.category is None
    assert result.weight == 0


def test_domain_word_alone_without_order_signal_does_not_match(matcher: Matcher):
    result = matcher.match("Ищу вдохновение, у кого какой лендинг любимый?")

    assert result.matched is False


def test_domain_plus_order_signal_matches_even_without_intent_phrase(matcher: Matcher):
    result = matcher.match("Есть бюджет на лендинг, разовая задача, кто возьмется?")

    assert result.matched is True
    assert result.is_order is True
    assert result.category == "domain"
    assert result.budget_flag is True
    assert "бюджет" in result.matched_order
    assert "разовая задача" in result.matched_order


def test_budget_flag_true_only_when_budget_keyword_present(matcher: Matcher):
    with_budget = matcher.match("Нужен сайт, бюджет обсуждаем")
    without_budget = matcher.match("Нужен сайт, срочно")

    assert with_budget.budget_flag is True
    assert without_budget.budget_flag is False


def test_intent_and_budget_together_sum_weight(matcher: Matcher):
    result = matcher.match("Нужен сайт, есть бюджет и готовое тз")

    assert result.matched is True
    assert result.category == "intent"
    # intent (3) + domain "сайт" (2) + budget "бюджет" + "тз" (1*2) = 7
    assert result.weight == 3 + 2 + 1 * 2
    assert result.budget_flag is True


def test_short_text_never_matches_even_with_keyword(matcher: Matcher):
    result = matcher.match("бот")

    assert result.matched is False


def test_empty_text_does_not_match(matcher: Matcher):
    result = matcher.match("")

    assert result.matched is False
    assert result.weight == 0


def test_short_domain_keyword_does_not_match_inside_longer_word():
    # "ии" is a real domain keyword (short for "искусственный интеллект"),
    # but plenty of unrelated Russian words end in "-ии" via case endings
    # (интеграции, информации, стоматологии, ...). Substring matching would
    # false-positive on all of them; whole-word matching must not.
    keywords = KeywordsConfig(
        intent=KeywordCategory(weight=3, keywords=["нужен сайт"]),
        domain=KeywordCategory(weight=2, keywords=["ии"]),
        budget=KeywordCategory(weight=1, keywords=["бюджет"]),
    )
    matcher = Matcher(keywords)

    result = matcher.match("Хочу больше узнать про интеграции разных систем, есть бюджет")

    assert "domain" not in result.matched_keywords
    assert result.matched is False  # domain keyword didn't actually fire, so no domain+order combo


def test_vacancy_post_rejected(matcher: Matcher):
    # Reads like a real hiring post: intent phrase fires ("ищу
    # разработчика" - ambiguous out of context), but every other signal
    # ("в штат", "оклад", "график работы") says this is an in-house job,
    # and there is no order_signals keyword anywhere to say otherwise.
    result = matcher.match(
        "Ищу разработчика в штат, оклад от 150000, график работы 5/2, полная занятость."
    )

    assert result.matched is False
    assert result.is_order is False
    assert result.rejected_reason == "hiring"
    assert "ищу разработчика" in result.matched_intent
    assert "в штат" in result.matched_anti
    assert result.matched_order == []


def test_vacancy_language_without_a_prior_match_is_not_flagged_as_rejected(matcher: Matcher):
    # Anti-hiring words alone, with nothing that would have matched anyway
    # (no intent, no domain), are just noise - not a "rejected" vacancy.
    # rejected_reason only makes sense for a post that would otherwise have
    # become a lead.
    result = matcher.match("У нас в штате дружный коллектив, встречаем закат вместе.")

    assert result.matched is False
    assert result.rejected_reason is None


def test_mixed_hiring_and_order_signal_passes_order_wins(matcher: Matcher):
    # Both a hiring marker ("в штат", "оклад") and an order signal
    # ("разовая задача") fire on the same post - order signals win the tie,
    # since a stray vacancy word is far less costly to a human reviewer
    # than a missed one-off order. See README "Баланс: лучше лишний лид".
    result = matcher.match(
        "Ищем на удаленку в штат добавить лендинг, это разовая задача, оклад не имеет значения."
    )

    assert result.matched is True
    assert result.is_order is True
    assert result.rejected_reason is None
    assert "в штат" in result.matched_anti
    assert "оклад" in result.matched_anti
    assert "разовая задача" in result.matched_order


def test_order_post_with_no_hiring_language_passes_cleanly(matcher: Matcher):
    result = matcher.match("Нужен сайт под ключ, бюджет 100000, пишите в лс за деталями.")

    assert result.matched is True
    assert result.is_order is True
    assert result.rejected_reason is None
    assert result.matched_anti == []
    assert "бюджет" in result.matched_order
