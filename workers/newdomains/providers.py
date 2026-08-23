"""Abstract provider interface for a newly-registered-domains feed, plus
one concrete implementation (WhoisXML) and one generic escape hatch
("custom") for wiring in a different compatible feed without writing a
new provider class.

Every provider normalizes to the same record shape regardless of the
underlying API's own field names:
    {"domain": str, "registered_date": str | None, "registrar": str | None}
"""

from abc import ABC, abstractmethod

import httpx
import structlog

log = structlog.get_logger(__name__)


class NewDomainsProvider(ABC):
    @abstractmethod
    async def fetch_new_domains(self, http_client: httpx.AsyncClient) -> list[dict]:
        """Returns freshly-registered domains as normalized dicts."""


class WhoisXmlNrdProvider(NewDomainsProvider):
    """WhoisXML API's "Newly Registered Domains" feed
    (https://newly-registered-domains.whoisxmlapi.com). Requires a paid
    subscription - see README.md for pricing and signup.

    IMPORTANT: this is built from WhoisXML's publicly documented API shape
    and has NOT been verified against a live key (the product requires
    payment, and no key was available while building this). Before
    trusting it in production, run it once against your own key and
    check the logged raw response shape matches what `_extract_records`
    expects - adjust the key names there if WhoisXML's API has since
    changed, or if your specific plan returns a different shape.
    """

    API_URL = "https://newly-registered-domains.whoisxmlapi.com/api/v1"

    def __init__(self, api_key: str, tlds: list[str] | None = None, date: str | None = None):
        self._api_key = api_key
        self._tlds = tlds or ["ru", "com"]
        # None = let the API default to the most recently available day.
        self._date = date

    async def fetch_new_domains(self, http_client: httpx.AsyncClient) -> list[dict]:
        records: list[dict] = []
        for tld in self._tlds:
            params = {"apiKey": self._api_key, "tlds": tld, "mode": "purchase"}
            if self._date:
                params["date"] = self._date

            try:
                response = await http_client.get(self.API_URL, params=params, timeout=30.0)
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPError as exc:
                log.error("newdomains.whoisxml_request_failed", tld=tld, error=str(exc))
                continue
            except ValueError as exc:  # invalid JSON
                log.error("newdomains.whoisxml_bad_response", tld=tld, error=str(exc))
                continue

            for raw_record in _extract_records(payload):
                domain = raw_record.get("domainName") or raw_record.get("domain")
                if not domain:
                    continue
                records.append({
                    "domain": domain,
                    "registered_date": raw_record.get("date") or raw_record.get("createdDate"),
                    "registrar": raw_record.get("registrarName") or raw_record.get("registrar"),
                })
        return records


def _extract_records(payload) -> list[dict]:
    """WhoisXML's NRD products have returned the domain list under a
    couple of different key names across product variants in their public
    docs - try each rather than assuming one, and fall back to empty
    (never raise) for a shape we don't recognize at all."""
    if not isinstance(payload, dict):
        return []
    for key in ("newRegisteredDomains", "domainsList", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


class CustomHttpProvider(NewDomainsProvider):
    """Generic escape hatch for a different NRD provider: GETs
    DOMAINS_API_URL with the key as a bearer token, expects either a bare
    JSON array or a {"domains": [...]}/{"results": [...]} envelope of
    {domain, registered_date, registrar} objects."""

    def __init__(self, api_key: str, api_url: str):
        self._api_key = api_key
        self._api_url = api_url

    async def fetch_new_domains(self, http_client: httpx.AsyncClient) -> list[dict]:
        try:
            response = await http_client.get(
                self._api_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30.0,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            log.error("newdomains.custom_provider_request_failed", error=str(exc))
            return []
        except ValueError as exc:
            log.error("newdomains.custom_provider_bad_response", error=str(exc))
            return []

        if isinstance(payload, dict):
            payload = payload.get("domains") or payload.get("results") or []
        if not isinstance(payload, list):
            return []

        records = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            domain = item.get("domain")
            if not domain:
                continue
            records.append({
                "domain": domain,
                "registered_date": item.get("registered_date"),
                "registrar": item.get("registrar"),
            })
        return records


def create_provider(settings) -> NewDomainsProvider | None:
    """None means "not configured" - the caller (main.py) treats that as
    the friendly-exit condition, never a crash."""
    provider_name = (settings.domains_provider or "").strip().lower()
    if not provider_name or not settings.domains_api_key:
        return None

    if provider_name == "whoisxml":
        return WhoisXmlNrdProvider(settings.domains_api_key, tlds=settings.tlds())

    if provider_name == "custom":
        if not settings.domains_api_url:
            log.error("newdomains.custom_provider_missing_url")
            return None
        return CustomHttpProvider(settings.domains_api_key, settings.domains_api_url)

    log.error("newdomains.unknown_provider", provider=provider_name)
    return None
