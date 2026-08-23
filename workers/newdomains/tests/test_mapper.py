"""Unit tests for mapper.py - hand-built fixture dicts, no network."""

from workers.newdomains.mapper import map_domain_to_lead


def test_maps_a_domain_with_a_real_site():
    record = {"domain": "freshbiz.ru", "registered_date": "2026-08-20", "registrar": "REG.RU"}

    lead = map_domain_to_lead(record, has_site=True)

    assert lead.name == "freshbiz.ru"
    assert lead.domain == "freshbiz.ru"
    assert lead.source == "NEW_DOMAIN"
    assert lead.has_site is True
    assert lead.raw == {"registered_date": "2026-08-20", "registrar": "REG.RU"}


def test_maps_a_domain_with_no_site_yet():
    record = {"domain": "justregistered.ru", "registered_date": "2026-08-23", "registrar": None}

    lead = map_domain_to_lead(record, has_site=False)

    assert lead.has_site is False


def test_contact_fields_are_left_blank():
    """WHOIS contact info is usually redacted (GDPR/registrar privacy) -
    never guessed."""
    record = {"domain": "example.ru"}

    lead = map_domain_to_lead(record, has_site=False)

    assert lead.phone is None
    assert lead.email is None


def test_missing_domain_yields_no_lead():
    assert map_domain_to_lead({}, has_site=False) is None
    assert map_domain_to_lead({"domain": None}, has_site=True) is None


def test_missing_optional_fields_do_not_crash():
    lead = map_domain_to_lead({"domain": "bare.ru"}, has_site=True)

    assert lead.raw["registered_date"] is None
    assert lead.raw["registrar"] is None
