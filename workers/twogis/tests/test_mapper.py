"""Unit tests for mapper.py - hand-built fixture dicts, no network."""

from workers.twogis.mapper import map_item_to_lead


def test_maps_basic_fields():
    item = {
        "id": "70000001029160997",
        "name": "Кофейня Ромашка",
        "address_name": "ул. Абая, 10",
        "contact_groups": [
            {"contacts": [{"type": "phone", "value": "+7 701 111 22 33"}]},
        ],
    }

    lead = map_item_to_lead(item, city="Алматы", rubric_name="Кафе")

    assert lead.name == "Кофейня Ромашка"
    assert lead.phone == "+7 701 111 22 33"
    assert lead.address == "ул. Абая, 10"
    assert lead.city == "Алматы"
    assert lead.source == "TWOGIS"
    assert lead.raw == {"rubric": "Кафе", "twogis_id": "70000001029160997"}


def test_has_site_true_when_website_contact_present():
    item = {
        "id": "1",
        "name": "Stroy Master",
        "contact_groups": [
            {"contacts": [
                {"type": "phone", "value": "+77021234567"},
                {"type": "website", "value": "https://stroymaster.kz"},
            ]},
        ],
    }

    lead = map_item_to_lead(item, city="Астана", rubric_name="Строительные компании")

    assert lead.has_site is True
    assert lead.domain == "https://stroymaster.kz"


def test_has_site_false_when_no_website_contact():
    item = {
        "id": "2",
        "name": "Салон Красоты Люкс",
        "contact_groups": [
            {"contacts": [{"type": "phone", "value": "+77051112233"}]},
        ],
    }

    lead = map_item_to_lead(item, city="Шымкент", rubric_name="Салоны красоты")

    assert lead.has_site is False
    assert lead.domain is None


def test_has_site_false_when_no_contact_groups_at_all():
    item = {"id": "3", "name": "Без контактов"}

    lead = map_item_to_lead(item, city="Караганда", rubric_name="Автосервисы")

    assert lead.has_site is False
    assert lead.phone is None
    assert lead.domain is None


def test_falls_back_to_link_contact_type_for_website():
    item = {
        "id": "4",
        "name": "Barbershop Point",
        "contact_groups": [
            {"contacts": [{"type": "link", "value": "https://barbershop-point.ru"}]},
        ],
    }

    lead = map_item_to_lead(item, city="Пермь", rubric_name="Барбершопы")

    assert lead.has_site is True
    assert lead.domain == "https://barbershop-point.ru"


def test_missing_name_falls_back_to_placeholder():
    item = {"id": "5"}

    lead = map_item_to_lead(item, city="Уфа", rubric_name="Кафе")

    assert lead.name == "Без названия"


def test_source_url_is_a_2gis_search_link_with_encoded_query():
    item = {"id": "6", "name": "Fitness Zone"}

    lead = map_item_to_lead(item, city="Омск", rubric_name="Фитнес-клубы")

    assert lead.source_url.startswith("https://2gis.ru/search/")
    assert "Fitness" in lead.source_url or "Fitness%20Zone" in lead.source_url
