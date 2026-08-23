"""Checks whether a freshly-registered domain already has a real site up,
or is just a parked/placeholder page (or nothing at all) - the strongest
version of the "no site" signal, since we're looking at the domain
minutes/hours/days after registration.
"""

import httpx
import structlog

log = structlog.get_logger(__name__)

# Common phrases on registrar/hosting parking pages and default
# "coming soon" placeholders - not exhaustive, but catches the large
# majority of non-sites without needing a paid page-classification API.
PARKING_MARKERS = (
    "domain is for sale",
    "this domain may be for sale",
    "buy this domain",
    "parked free",
    "parking page",
    "domain parking",
    "sedo",
    "godaddy.com/domains",
    "coming soon",
    "future home of something quite cool",
    "under construction",
    "website coming soon",
    "this website is for sale",
)

# A real site almost always has more than this much markup; a parked
# page's actual content (once you strip out ad/redirect boilerplate) is
# often surprisingly short.
MIN_REAL_CONTENT_LENGTH = 200


def looks_parked_or_empty(status_code: int, body: str | None) -> bool:
    """Pure: given a response already fetched elsewhere, decides if it
    reads as "no real site yet"."""
    if status_code >= 400:
        return True
    text = (body or "").strip().lower()
    if len(text) < MIN_REAL_CONTENT_LENGTH:
        return True
    return any(marker in text for marker in PARKING_MARKERS)


async def check_has_site(domain: str, http_client: httpx.AsyncClient, timeout: float = 8.0) -> bool:
    """Tries https then http; a domain that doesn't even resolve/connect
    on either scheme has no site by definition. Never raises - any
    connection problem is treated the same as "no site"."""
    for scheme in ("https", "http"):
        try:
            response = await http_client.get(f"{scheme}://{domain}", timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            log.debug("newdomains.site_check_connection_failed", domain=domain, scheme=scheme, error=str(exc))
            continue
        return not looks_parked_or_empty(response.status_code, response.text)
    return False
