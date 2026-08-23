"""Unit tests for mapper.py - hand-built fixture dicts matching the real
Overpass "out tags" element shape ({type, id, tags}), no network."""

from workers.osm.mapper import map_element_to_lead, normalize_domain


def test_maps_basic_fields_from_a_node():
    element = {
        "type": "node",
        "id": 123456789,
        "tags": {
            "amenity": "cafe",
            "name": "Кофейня Ромашка",
            "addr:street": "ул. Абая",
            "addr:housenumber": "10",
            "contact:phone": "+7 701 111 22 33",
            "website": "https://romashka-coffee.kz",
        },
    }

    lead = map_element_to_lead(element, city="Алматы", category_name="Кафе")

    assert lead.name == "Кофейня Ромашка"
    assert lead.phone == "+7 701 111 22 33"
    assert lead.address == "ул. Абая, 10"
    assert lead.city == "Алматы"
    assert lead.source == "OSM"
    assert lead.domain == "romashka-coffee.kz"
    assert lead.has_site is True
    assert lead.source_url == "https://www.openstreetmap.org/node/123456789"
    assert lead.raw == {"osm_id": 123456789, "osm_type": "node", "category_tag": "Кафе"}


def test_maps_a_way_and_a_relation_the_same_way():
    way = {"type": "way", "id": 1, "tags": {"name": "Way Cafe"}}
    relation = {"type": "relation", "id": 2, "tags": {"name": "Relation Cafe"}}

    way_lead = map_element_to_lead(way, city="Москва", category_name="Кафе")
    relation_lead = map_element_to_lead(relation, city="Москва", category_name="Кафе")

    assert way_lead.source_url == "https://www.openstreetmap.org/way/1"
    assert relation_lead.source_url == "https://www.openstreetmap.org/relation/2"


def test_skips_elements_with_no_name():
    element = {"type": "node", "id": 1, "tags": {"amenity": "cafe"}}

    lead = map_element_to_lead(element, city="Казань", category_name="Кафе")

    assert lead is None


def test_skips_elements_with_no_tags_at_all():
    element = {"type": "node", "id": 1}

    lead = map_element_to_lead(element, city="Казань", category_name="Кафе")

    assert lead is None


def test_has_site_true_when_website_tag_present():
    element = {"type": "node", "id": 1, "tags": {"name": "Stroy Master", "website": "https://stroymaster.kz"}}

    lead = map_element_to_lead(element, city="Астана", category_name="Автосервисы")

    assert lead.has_site is True
    assert lead.domain == "stroymaster.kz"


def test_has_site_false_when_no_website_tag():
    element = {"type": "node", "id": 1, "tags": {"name": "Салон Красоты Люкс"}}

    lead = map_element_to_lead(element, city="Шымкент", category_name="Салоны красоты")

    assert lead.has_site is False
    assert lead.domain is None


def test_prefers_contact_website_over_bare_website():
    element = {
        "type": "node",
        "id": 1,
        "tags": {"name": "X", "contact:website": "https://contact-variant.kz", "website": "https://plain-variant.kz"},
    }

    lead = map_element_to_lead(element, city="Алматы", category_name="Кафе")

    assert lead.domain == "contact-variant.kz"


def test_prefers_contact_phone_over_bare_phone():
    element = {
        "type": "node",
        "id": 1,
        "tags": {"name": "X", "contact:phone": "+77011112233", "phone": "+77099998877"},
    }

    lead = map_element_to_lead(element, city="Алматы", category_name="Кафе")

    assert lead.phone == "+77011112233"


def test_address_falls_back_to_street_only_when_no_housenumber():
    element = {"type": "node", "id": 1, "tags": {"name": "X", "addr:street": "ул. Пушкина"}}

    lead = map_element_to_lead(element, city="Алматы", category_name="Кафе")

    assert lead.address == "ул. Пушкина"


def test_address_is_none_when_no_addr_tags():
    element = {"type": "node", "id": 1, "tags": {"name": "X"}}

    lead = map_element_to_lead(element, city="Алматы", category_name="Кафе")

    assert lead.address is None


def test_normalize_domain_strips_scheme_and_www():
    assert normalize_domain("https://www.Example.kz/path?x=1") == "example.kz"
    assert normalize_domain("example.kz") == "example.kz"
    assert normalize_domain("") is None
    assert normalize_domain(None) is None
