"""Unit tests for mapper.py - hand-built fixture dicts matching the real
Places API (New) Text Search response shape (displayName.text,
formattedAddress, nationalPhoneNumber, websiteUri, types), no network."""

from workers.google_places.mapper import map_place_to_lead, normalize_domain


def test_maps_basic_fields_from_realistic_response_shape():
    place = {
        "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
        "displayName": {"text": "Кофейня Ромашка", "languageCode": "ru"},
        "formattedAddress": "ул. Абая, 10, Алматы, Казахстан",
        "nationalPhoneNumber": "+7 701 111-22-33",
        "websiteUri": "https://romashka-coffee.kz",
        "types": ["cafe", "food", "point_of_interest", "establishment"],
    }

    lead = map_place_to_lead(place, city="Алматы", category_name="Кафе")

    assert lead.name == "Кофейня Ромашка"
    assert lead.phone == "+7 701 111-22-33"
    assert lead.address == "ул. Абая, 10, Алматы, Казахстан"
    assert lead.city == "Алматы"
    assert lead.source == "GOOGLE_MAPS"
    assert lead.domain == "romashka-coffee.kz"
    assert lead.has_site is True
    assert lead.source_url == "https://www.google.com/maps/place/?q=place_id:ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert lead.raw == {
        "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
        "types": ["cafe", "food", "point_of_interest", "establishment"],
        "category_query": "Кафе",
    }


def test_skips_places_with_no_display_name():
    place = {"id": "1", "formattedAddress": "somewhere"}

    lead = map_place_to_lead(place, city="Астана", category_name="Кафе")

    assert lead is None


def test_skips_places_with_empty_display_name_text():
    place = {"id": "1", "displayName": {"text": ""}}

    lead = map_place_to_lead(place, city="Астана", category_name="Кафе")

    assert lead is None


def test_has_site_true_when_website_uri_present():
    place = {"id": "1", "displayName": {"text": "Stroy Master"}, "websiteUri": "https://stroymaster.kz"}

    lead = map_place_to_lead(place, city="Астана", category_name="Строительные компании")

    assert lead.has_site is True
    assert lead.domain == "stroymaster.kz"


def test_has_site_false_when_no_website_uri():
    place = {"id": "1", "displayName": {"text": "Салон Красоты Люкс"}}

    lead = map_place_to_lead(place, city="Шымкент", category_name="Салоны красоты")

    assert lead.has_site is False
    assert lead.domain is None


def test_missing_phone_and_address_do_not_crash():
    place = {"id": "1", "displayName": {"text": "Минимальная запись"}}

    lead = map_place_to_lead(place, city="Омск", category_name="Кафе")

    assert lead.phone is None
    assert lead.address is None


def test_missing_place_id_yields_no_source_url():
    place = {"displayName": {"text": "Без id"}}

    lead = map_place_to_lead(place, city="Омск", category_name="Кафе")

    assert lead.source_url is None
    assert lead.raw["place_id"] is None


def test_missing_types_defaults_to_empty_list():
    place = {"id": "1", "displayName": {"text": "Без типов"}}

    lead = map_place_to_lead(place, city="Омск", category_name="Кафе")

    assert lead.raw["types"] == []


def test_normalize_domain_strips_scheme_and_www():
    assert normalize_domain("https://www.Example.kz/path?x=1") == "example.kz"
    assert normalize_domain("example.kz") == "example.kz"
    assert normalize_domain("") is None
    assert normalize_domain(None) is None
