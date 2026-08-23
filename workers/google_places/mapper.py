"""Pure mapping: one Places API (New) place (a dict, as returned in
payload["places"]) -> one RawCompanyRequest for the core, or None if the
place has no display name. No I/O, no network - trivial to unit test on
hand-built fixture dicts.
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


def map_place_to_lead(place: dict, city: str, category_name: str) -> RawCompanyRequest | None:
    display_name = (place.get("displayName") or {}).get("text")
    if not display_name:
        return None

    place_id = place.get("id")
    website = place.get("websiteUri")
    domain = normalize_domain(website)

    return RawCompanyRequest(
        name=display_name,
        domain=domain,
        phone=place.get("nationalPhoneNumber"),
        address=place.get("formattedAddress"),
        city=city,
        source="GOOGLE_MAPS",
        source_url=f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None,
        # Text Search reliably returns websiteUri when Google has one on
        # file, so its absence is a real "no site" signal.
        has_site=bool(domain),
        raw={
            "place_id": place_id,
            "types": place.get("types") or [],
            "category_query": category_name,
        },
    )
