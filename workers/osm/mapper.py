"""Pure mapping: one Overpass element (a dict, as returned in
payload["elements"]) -> one RawCompanyRequest for the core, or None if the
element has no name (nothing meaningful to send). No I/O, no network -
trivial to unit test on hand-built fixture dicts.
"""

import re

from .core_client import RawCompanyRequest

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_WWW_RE = re.compile(r"^www\.", re.IGNORECASE)


def normalize_domain(raw: str | None) -> str | None:
    """Strip scheme/www/path down to a bare host, mirroring the Java
    core's NormalizationUtil.normalizeDomain closely enough that both
    sides agree on what "the same domain" looks like."""
    if not raw or not raw.strip():
        return None
    value = raw.strip().lower()
    value = _SCHEME_RE.sub("", value)
    value = _WWW_RE.sub("", value)
    value = value.split("/")[0].split("?")[0].split("#")[0]
    return value or None


def _address(tags: dict) -> str | None:
    street = tags.get("addr:street")
    housenumber = tags.get("addr:housenumber")
    if street and housenumber:
        return f"{street}, {housenumber}"
    return street or housenumber or None


def _osm_url(osm_type: str | None, osm_id) -> str | None:
    """OSM gives every element a real, permanent, always-resolvable
    permalink - unlike some other sources, no guessing needed here."""
    if not osm_type or osm_id is None:
        return None
    return f"https://www.openstreetmap.org/{osm_type}/{osm_id}"


def map_element_to_lead(element: dict, city: str, category_name: str) -> RawCompanyRequest | None:
    tags = element.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None  # nothing worth sending without a name

    phone = tags.get("contact:phone") or tags.get("phone")
    website_raw = tags.get("contact:website") or tags.get("website")
    domain = normalize_domain(website_raw)
    osm_type = element.get("type")
    osm_id = element.get("id")

    return RawCompanyRequest(
        name=name,
        domain=domain,
        phone=phone,
        address=_address(tags),
        city=city,
        source="OSM",
        source_url=_osm_url(osm_type, osm_id),
        # OSM contributors tag what they actually observe - unlike some
        # other sources, an absent website tag is a genuinely reliable
        # "no site" signal, not an artifact of API/plan limitations.
        has_site=bool(domain),
        raw={
            "osm_id": osm_id,
            "osm_type": osm_type,
            "category_tag": category_name,
        },
    )
