"""Unit tests for mapper.py - hand-built fixture dicts (as produced by
parser.py's parse_search_html/parse_purchase_card), no network."""

from workers.zakupki.mapper import map_purchase_to_lead, merge_purchase_with_details

# --- merge_purchase_with_details() -----------------------------------------


def test_merge_prefers_detail_page_subject_and_budget():
    purchase = {"subject": "truncated on the list page...", "price": "1 000 000,00 ₽", "region": None, "inn": None}
    details = {
        "subject": "Full subject from the detail page",
        "budget": 1500000.0,
        "deadline": "28.08.2026",
        "law_type": "44-ФЗ",
        "customer_inn": "6663012345",
        "customer_phone": "7-495-9579977",
        "region": "Свердловская область",
    }

    merged = merge_purchase_with_details(purchase, details)

    assert merged["subject"] == "Full subject from the detail page"
    assert merged["budget"] == 1500000.0
    assert merged["deadline"] == "28.08.2026"
    assert merged["law_type"] == "44-ФЗ"
    assert merged["customer_inn"] == "6663012345"
    assert merged["customer_phone"] == "7-495-9579977"
    assert merged["region"] == "Свердловская область"


def test_merge_falls_back_to_list_page_subject_when_detail_page_has_none():
    purchase = {"subject": "list page subject", "price": None, "region": None, "inn": None}
    details = {"subject": None, "budget": None, "deadline": None, "law_type": None,
               "customer_inn": None, "customer_phone": None, "region": None}

    merged = merge_purchase_with_details(purchase, details)

    assert merged["subject"] == "list page subject"


def test_merge_falls_back_to_parsing_list_page_price_when_detail_budget_missing():
    purchase = {"price": "1 500 000,00 ₽", "region": None, "inn": None}
    details = {"budget": None, "subject": None, "deadline": None, "law_type": None,
               "customer_inn": None, "customer_phone": None, "region": None}

    merged = merge_purchase_with_details(purchase, details)

    assert merged["budget"] == 1500000.0


def test_merge_keeps_list_page_region_over_detail_page_region():
    """The list page's own region (when it has one) is already
    trustworthy - the detail page's is only a fallback."""
    purchase = {"region": "Список: Москва", "price": None, "inn": None}
    details = {"region": "Карточка: Москва", "budget": None, "subject": None,
               "deadline": None, "law_type": None, "customer_inn": None, "customer_phone": None}

    merged = merge_purchase_with_details(purchase, details)

    assert merged["region"] == "Список: Москва"


def test_merge_prefers_detail_page_inn_over_list_page_inn():
    purchase = {"inn": "1111111111", "price": None, "region": None}
    details = {"customer_inn": "2222222222", "budget": None, "subject": None,
               "deadline": None, "law_type": None, "customer_phone": None, "region": None}

    merged = merge_purchase_with_details(purchase, details)

    assert merged["customer_inn"] == "2222222222"


def test_merge_falls_back_to_list_page_inn_when_detail_page_has_none():
    purchase = {"inn": "1111111111", "price": None, "region": None}
    details = {"customer_inn": None, "budget": None, "subject": None,
               "deadline": None, "law_type": None, "customer_phone": None, "region": None}

    merged = merge_purchase_with_details(purchase, details)

    assert merged["customer_inn"] == "1111111111"


def test_merge_with_no_details_still_parses_list_page_price_into_budget():
    """When FETCH_DETAILS=false (or the detail fetch failed/was skipped),
    details is None - budget must still come out as a number, not the raw
    price string."""
    purchase = {"price": "728 800,00 ₽", "region": "Нижегородская область", "inn": "5250000021"}

    merged = merge_purchase_with_details(purchase, details=None)

    assert merged["budget"] == 728800.0
    assert merged["deadline"] is None
    assert merged["law_type"] is None
    assert merged["customer_inn"] == "5250000021"
    assert merged["customer_phone"] is None
    assert merged["region"] == "Нижегородская область"  # untouched


# --- map_purchase_to_lead() -------------------------------------------------


def test_maps_a_fully_enriched_purchase():
    purchase = {
        "reg_number": "0173200001426001536",
        "detail_url": "https://zakupki.gov.ru/epz/order/notice/zk20/view/common-info.html?regNumber=123",
        "subject": "Оказание комплекса услуг по разработке мини-игры",
        "region": "Москва",
        "customer_name": "ДЕПАРТАМЕНТ ГОРОДА МОСКВЫ ПО КОНКУРЕНТНОЙ ПОЛИТИКЕ",
        "budget": 8398003.33,
        "deadline": "28.08.2026",
        "law_type": "44-ФЗ",
        "customer_inn": "7704515009",
        "customer_phone": "7-495-9579977",
    }

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.name == "ДЕПАРТАМЕНТ ГОРОДА МОСКВЫ ПО КОНКУРЕНТНОЙ ПОЛИТИКЕ"
    assert lead.phone == "7-495-9579977"
    assert lead.city == "Москва"
    assert lead.source == "ZAKUPKI"
    assert lead.source_url == purchase["detail_url"]
    assert lead.has_site is True
    assert lead.raw == {
        "budget": 8398003.33,
        "subject": "Оказание комплекса услуг по разработке мини-игры",
        "inn": "7704515009",
        "keyword": "разработка сайта",
        "reg_number": "0173200001426001536",
        "deadline": "28.08.2026",
        "law_type": "44-ФЗ",
        "budgetMentioned": True,
    }


def test_skips_purchases_with_no_customer_name():
    purchase = {"reg_number": "1", "subject": "Что-то", "customer_name": None}

    lead = map_purchase_to_lead(purchase, keyword="автоматизация")

    assert lead is None


def test_skips_purchases_with_blank_customer_name():
    purchase = {"reg_number": "1", "customer_name": ""}

    lead = map_purchase_to_lead(purchase, keyword="автоматизация")

    assert lead is None


def test_has_site_is_always_true():
    """The customer is a real government/municipal body - reporting
    hasSite=false would falsely trigger the core's +40 "no site" rule."""
    purchase = {"reg_number": "1", "customer_name": "Администрация города Х"}

    lead = map_purchase_to_lead(purchase, keyword="внедрение")

    assert lead.has_site is True


def test_budget_mentioned_is_always_true_when_lead_is_created():
    """Unlike a casually mentioned budget, an НМЦК is real and approved -
    this is exactly what the core's budgetMentioned rule (+20) rewards."""
    purchase = {"reg_number": "1", "customer_name": "МКУ Х", "budget": None}

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.raw["budgetMentioned"] is True
    assert lead.raw["budget"] is None


def test_no_customer_phone_leaves_lead_phone_blank():
    purchase = {"reg_number": "1", "customer_name": "МКУ Х", "customer_phone": None}

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.phone is None


def test_falls_back_to_bare_inn_key_when_customer_inn_absent():
    """map_purchase_to_lead accepts either shape for the INN, in case it's
    ever called with an un-merged (no detail page) dict directly."""
    purchase = {"reg_number": "1", "customer_name": "МКУ Х", "inn": "1234567890"}

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.raw["inn"] == "1234567890"


def test_missing_optional_fields_do_not_crash():
    purchase = {"reg_number": "1", "customer_name": "МКУ Х"}

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.city is None
    assert lead.source_url is None
    assert lead.raw["subject"] is None
    assert lead.raw["inn"] is None
    assert lead.raw["deadline"] is None
    assert lead.raw["law_type"] is None
