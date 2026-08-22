"""Pure mapping: one 2GIS Catalog API item (a dict, as returned in
result.items[]) -> one RawCompanyRequest for the core. No I/O, no network -
trivial to unit test on hand-built fixture dicts.
"""

from urllib.parse import quote

from .core_client import RawCompanyRequest


def _first_contact_value(item: dict, contact_type: str) -> str | None:
    """2GIS groups contacts as item.contact_groups[].contacts[], each a
    {"type": "phone"|"website"|..., "value": "..."}. Returns the first
    match for the given type, or None."""
    for group in item.get("contact_groups") or []:
        for contact in group.get("contacts") or []:
            if contact.get("type") == contact_type:
                value = contact.get("value") or contact.get("text")
                if value:
                    return value
    return None


def _address(item: dict) -> str | None:
    return item.get("address_name") or item.get("address_comment") or item.get("full_name")


def _twogis_search_url(name: str, city: str) -> str:
    """2GIS's Catalog API doesn't reliably expose a direct firm-page
    permalink in this response shape, so we link back to a 2GIS site
    search for the name+city instead - a genuine, always-resolvable URL
    rather than a guessed firm-page path."""
    return f"https://2gis.ru/search/{quote(f'{name} {city}')}"


def map_item_to_lead(item: dict, city: str, rubric_name: str) -> RawCompanyRequest:
    twogis_id = str(item.get("id") or "")
    name = item.get("name") or "Без названия"
    phone = _first_contact_value(item, "phone")
    website = _first_contact_value(item, "website") or _first_contact_value(item, "link")
    has_site = bool(website)

    return RawCompanyRequest(
        name=name,
        domain=website,
        phone=phone,
        address=_address(item),
        city=city,
        source="TWOGIS",
        source_url=_twogis_search_url(name, city),
        has_site=has_site,
        raw={"rubric": rubric_name, "twogis_id": twogis_id},
    )
