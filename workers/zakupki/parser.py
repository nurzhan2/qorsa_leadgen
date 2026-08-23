"""Pure HTML parsing of zakupki.gov.ru's public search results page. No
network - takes a raw HTML string, returns a list of plain dicts.

There is no official JSON API for this search (see README.md), so this
parses the page's markup directly using BeautifulSoup. Every field lookup
is wrapped defensively and field lookups are done BY LABEL TEXT
(".registry-entry__body-title" contents), not by position, so a reordered
field doesn't silently misattribute a value to the wrong key - it just
comes back as None. A single malformed/unexpected card is skipped (and
logged) rather than raising and killing the whole page's worth of results.

This is inherently fragile: it depends on zakupki.gov.ru's current HTML
structure, which the site's operators can change at any time without
notice. See README.md "Хрупкость парсера" for what breaking looks like
and how to fix it.
"""

import re

import structlog
from bs4 import BeautifulSoup

log = structlog.get_logger(__name__)

BASE_URL = "https://zakupki.gov.ru"
CARD_SELECTOR = ".search-registry-entry-block"


def parse_search_html(html: str) -> list[dict]:
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(CARD_SELECTOR)

    purchases = []
    for card in cards:
        try:
            purchase = _parse_card(card)
        except Exception as exc:  # noqa: BLE001 - one bad card must never kill the whole run
            log.warning("zakupki.card_parse_failed", error=str(exc))
            continue
        if purchase is not None:
            purchases.append(purchase)
    return purchases


def _text_or_none(element) -> str | None:
    if element is None:
        return None
    text = element.get_text(strip=True)
    return text or None


def _field_by_label(card, label_keyword: str) -> str | None:
    """Scans every .registry-entry__body-block (a label+value pair) for
    one whose label contains `label_keyword` (case-insensitive), and
    returns its value text. Looking up by label text rather than position
    is what keeps this working if the site reorders fields."""
    for block in card.select(".registry-entry__body-block"):
        label = _text_or_none(block.select_one(".registry-entry__body-title"))
        if label and label_keyword.lower() in label.lower():
            return _text_or_none(block.select_one(".registry-entry__body-value"))
    return None


def _parse_card(card) -> dict | None:
    reg_number = None
    detail_url = None
    try:
        number_link = card.select_one(".registry-entry__header-mid__number a")
        if number_link is not None:
            reg_number = re.sub(r"\D", "", number_link.get_text(strip=True)) or None
            href = number_link.get("href")
            if href:
                detail_url = href if href.startswith("http") else BASE_URL + href
    except Exception as exc:  # noqa: BLE001
        log.warning("zakupki.field_parse_failed", field="reg_number", error=str(exc))

    if not reg_number:
        # Nothing to dedup on or point sourceUrl at - not worth a lead.
        return None

    subject = _safe_field(card, lambda c: _field_by_label(c, "предмет"), "subject")
    region = _safe_field(card, lambda c: _field_by_label(c, "регион"), "region")

    customer_name = None
    inn = None
    try:
        customer_link = card.select_one(".registry-entry__body-href a")
        if customer_link is not None:
            customer_name = _text_or_none(customer_link)
            href = customer_link.get("href") or ""
            match = re.search(r"[?&]inn=(\d+)", href)
            if match:
                inn = match.group(1)
    except Exception as exc:  # noqa: BLE001
        log.warning("zakupki.field_parse_failed", field="customer", error=str(exc))

    price = _safe_field(card, lambda c: _text_or_none(c.select_one(".price-block__value")), "price")

    return {
        "reg_number": reg_number,
        "detail_url": detail_url,
        "subject": subject,
        "region": region,
        "customer_name": customer_name,
        "inn": inn,
        "price": price,
    }


def _safe_field(card, extractor, field_name: str):
    try:
        return extractor(card)
    except Exception as exc:  # noqa: BLE001 - a single field must never take down the whole card
        log.warning("zakupki.field_parse_failed", field=field_name, error=str(exc))
        return None
