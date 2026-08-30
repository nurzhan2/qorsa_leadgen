"""Pure mapping: one aggregated employer (+ optional employer details) ->
one RawCompanyRequest for the core. No I/O, no network - trivially testable
on hand-built dicts.

Field names follow HH's OpenAPI spec exactly (read while building this):
vacancy.{name, alternate_url, published_at, area.name, salary, employer.*},
employer.{site_url, type}.
"""

import re

from .core_client import RawCompanyRequest
from .dedup import EmployerBucket

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_WWW_RE = re.compile(r"^www\.", re.IGNORECASE)


def normalize_domain(raw: str | None) -> str | None:
    """Strip scheme/www/path down to a bare host, mirroring the Java core's
    NormalizationUtil.normalizeDomain closely enough that both sides agree
    on what "the same domain" is (the core dedups on it)."""
    if not raw or not raw.strip():
        return None
    value = raw.strip().lower()
    value = _SCHEME_RE.sub("", value)
    value = _WWW_RE.sub("", value)
    value = value.split("/")[0].split("?")[0].split("#")[0]
    return value or None


def has_salary(vacancy: dict) -> bool:
    """HH's `salary` is nullable, and an object with both bounds null is
    effectively "not specified" - treat that as no salary rather than
    claiming a budget signal we don't have.

    Newer payloads also carry `salary_range`; it's accepted as an
    alternative source of the same signal.
    """
    for key in ("salary", "salary_range"):
        block = vacancy.get(key)
        if isinstance(block, dict) and (block.get("from") is not None or block.get("to") is not None):
            return True
    return False


def salary_summary(vacancy: dict) -> dict | None:
    """Compact salary for the raw payload - not used for scoring, just so a
    human reading the lead can see the number."""
    for key in ("salary", "salary_range"):
        block = vacancy.get(key)
        if isinstance(block, dict) and (block.get("from") is not None or block.get("to") is not None):
            return {
                "from": block.get("from"),
                "to": block.get("to"),
                "currency": block.get("currency"),
                "gross": block.get("gross"),
            }
    return None


def _city(vacancy: dict) -> str | None:
    area = vacancy.get("area")
    if isinstance(area, dict):
        return area.get("name") or None
    return None


def map_employer_to_lead(
    bucket: EmployerBucket,
    stale_days: int,
    employer_details: dict | None = None,
) -> RawCompanyRequest | None:
    """One employer -> one lead, or None when there's no company name to
    lead with (nothing meaningful to send)."""
    vacancy = bucket.best or {}
    employer = vacancy.get("employer") or {}
    name = (employer_details or {}).get("name") or employer.get("name") or bucket.employer_name
    if not name or not str(name).strip():
        return None

    site_url = (employer_details or {}).get("site_url")
    domain = normalize_domain(site_url)
    salary = salary_summary(vacancy)

    return RawCompanyRequest(
        name=str(name).strip(),
        domain=domain,
        city=_city(vacancy),
        source="HH",
        # The STALEST vacancy - the one that best evidences "can't hire",
        # and therefore the one worth opening first.
        source_url=vacancy.get("alternate_url"),
        # Only claim a site when the employer actually published one. HH
        # leaves site_url empty far more often than companies truly lack a
        # site, so a false `false` here would wrongly fire the core's
        # "+40 no site" rule - see README.md "Честно про ограничения".
        has_site=bool(domain),
        raw={
            "vacancy_name": vacancy.get("name"),
            "vacancy_url": vacancy.get("alternate_url"),
            "published_at": vacancy.get("published_at"),
            "age_days": bucket.best_age,
            "is_stale": bucket.has_stale,
            "stale_threshold_days": stale_days,
            "employer_id": bucket.employer_id,
            "open_vacancies_matched": bucket.vacancy_count,
            "keyword": bucket.keywords[0] if bucket.keywords else None,
            "keywords": bucket.keywords,
            "salary": salary,
            # The core's ScoringService reads this directly (+20). A quoted
            # salary is a real, approved budget - exactly what the rule is
            # meant to reward.
            "budgetMentioned": bucket.has_salary,
            "employer_type": (employer_details or {}).get("type"),
            "accredited_it_employer": employer.get("accredited_it_employer"),
        },
    )
