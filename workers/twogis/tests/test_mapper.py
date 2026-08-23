"""Unit tests for mapper.py - hand-built fixture dicts matching the real
2GIS /3.0/items response shape (contact_groups, address_name, full_name,
point, rubrics), no network."""

from workers.twogis.mapper import map_item_to_lead


def test_maps_basic_fields_from_realistic_response_shape():
    # Shape matches a real GET /3.0/items response with
    # fields=items.contact_groups,items.address_name,items.full_name,items.point,items.rubrics
    item = {
        "id": "70000001029160997",
        "name": "Ромашка",
        "full_name": "Кофейня Ромашка",
        "address_name": "ул. Абая, 10",
        "point": {"lat": 43.238293, "lon": 76.945465},
        "contact_groups": [
            {"contacts": [{"type": "phone", "value": "+7 701 111 22 33"}]},
        ],
        "rubrics": [{"id": "164", "name": "Кафе"}],
    }

    lead = map_item_to_lead(item, city="Алматы", rubric_name="Кафе")

    # full_name is preferred over the bare name when both are present.
    assert lead.name == "Кофейня Ромашка"
    assert lead.phone == "+7 701 111 22 33"
    assert lead.address == "ул. Абая, 10"
    assert lead.city == "Алматы"
    assert lead.source == "TWOGIS"
    assert lead.raw == {
        "twogis_id": "70000001029160997",
        "rubrics": [{"id": "164", "name": "Кафе"}],
        "rubric_query": "Кафе",
    }


def test_falls_back_to_name_when_full_name_missing():
    item = {"id": "1", "name": "Stroy Master"}

    lead = map_item_to_lead(item, city="Астана", rubric_name="Строительные компании")

    assert lead.name == "Stroy Master"


def test_has_site_true_when_website_contact_present():
    item = {
        "id": "1",
        "full_name": "Stroy Master",
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
        "full_name": "Салон Красоты Люкс",
        "contact_groups": [
            {"contacts": [{"type": "phone", "value": "+77051112233"}]},
        ],
    }

    lead = map_item_to_lead(item, city="Шымкент", rubric_name="Салоны красоты")

    assert lead.has_site is False
    assert lead.domain is None


def test_has_site_false_when_contact_groups_missing_entirely():
    """contact_groups can legitimately be absent from the response - must
    not raise, and must be treated the same as "no website found"."""
    item = {"id": "3", "full_name": "Без контактов"}

    lead = map_item_to_lead(item, city="Караганда", rubric_name="Автосервисы")

    assert lead.has_site is False
    assert lead.phone is None
    assert lead.domain is None


def test_has_site_false_when_contact_groups_is_an_empty_list():
    item = {"id": "3b", "full_name": "Пустые контакты", "contact_groups": []}

    lead = map_item_to_lead(item, city="Караганда", rubric_name="Автосервисы")

    assert lead.has_site is False


def test_has_site_false_when_contacts_list_is_empty_inside_a_group():
    item = {"id": "3c", "full_name": "Группа без контактов", "contact_groups": [{"contacts": []}]}

    lead = map_item_to_lead(item, city="Караганда", rubric_name="Автосервисы")

    assert lead.has_site is False
    assert lead.phone is None


def test_falls_back_to_link_contact_type_for_website():
    item = {
        "id": "4",
        "full_name": "Barbershop Point",
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


def test_missing_address_name_yields_none_not_a_crash():
    item = {"id": "6", "full_name": "Кафе без адреса"}

    lead = map_item_to_lead(item, city="Омск", rubric_name="Кафе")

    assert lead.address is None


def test_source_url_is_a_2gis_search_link_with_encoded_query():
    item = {"id": "7", "full_name": "Fitness Zone"}

    lead = map_item_to_lead(item, city="Омск", rubric_name="Фитнес-клубы")

    assert lead.source_url.startswith("https://2gis.ru/search/")
    assert "Fitness%20Zone" in lead.source_url


def test_city_comes_from_config_not_from_the_api_response():
    """The 2GIS item response doesn't reliably echo the city back - it
    must always be whatever the caller (cities.yml) says it searched."""
    item = {"id": "8", "full_name": "Кафе Икс", "address_name": "где-то в Москве"}

    lead = map_item_to_lead(item, city="Санкт-Петербург", rubric_name="Кафе")

    assert lead.city == "Санкт-Петербург"
