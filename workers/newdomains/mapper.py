"""Pure mapping: one normalized domain record (from a provider) plus its
site-check result -> one RawCompanyRequest for the core, or None if
there's no domain to lead with. No I/O, no network - trivial to unit test
on hand-built fixture dicts.
"""

from .core_client import RawCompanyRequest


def map_domain_to_lead(record: dict, has_site: bool) -> RawCompanyRequest | None:
    domain = record.get("domain")
    if not domain:
        return None

    return RawCompanyRequest(
        name=domain,
        domain=domain,
        # Contact info from WHOIS is usually redacted (GDPR/registrar
        # privacy) - left blank rather than guessed. The domain itself is
        # still a useful lead: someone just started a business and hasn't
        # built a site (or has, and hasSite reflects that).
        source="NEW_DOMAIN",
        has_site=has_site,
        raw={
            "registered_date": record.get("registered_date"),
            "registrar": record.get("registrar"),
        },
    )
