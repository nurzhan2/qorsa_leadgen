"""Unit tests for mapper.py - hand-built fixture dicts (as produced by
parser.py), no network."""

from workers.zakupki.mapper import map_purchase_to_lead


def test_maps_a_full_purchase():
    purchase = {
        "reg_number": "0123456789012345678",
        "detail_url": "https://zakupki.gov.ru/epz/order/notice/ea44/notice/common-info.html?regNumber=123",
        "subject": "Разработка сайта для муниципального учреждения",
        "region": "Свердловская область",
        "customer_name": 'МБУ "Городской информационный центр"',
        "inn": "6663012345",
        "price": "1 500 000,00 ₽",
    }

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.name == 'МБУ "Городской информационный центр"'
    assert lead.city == "Свердловская область"
    assert lead.source == "ZAKUPKI"
    assert lead.source_url == purchase["detail_url"]
    assert lead.has_site is True
    assert lead.raw == {
        "budget": "1 500 000,00 ₽",
        "subject": "Разработка сайта для муниципального учреждения",
        "inn": "6663012345",
        "keyword": "разработка сайта",
        "reg_number": "0123456789012345678",
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
    purchase = {"reg_number": "1", "customer_name": "МКУ Х", "price": None}

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.raw["budgetMentioned"] is True
    assert lead.raw["budget"] is None


def test_missing_optional_fields_do_not_crash():
    purchase = {"reg_number": "1", "customer_name": "МКУ Х"}

    lead = map_purchase_to_lead(purchase, keyword="разработка сайта")

    assert lead.city is None
    assert lead.source_url is None
    assert lead.raw["subject"] is None
    assert lead.raw["inn"] is None
