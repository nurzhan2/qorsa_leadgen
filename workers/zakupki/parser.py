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


# --- Purchase detail/card page (a SECOND page per purchase - see README) ---
#
# The detail page uses TWO DIFFERENT label/value CSS class pairs in
# different sections of the same page (verified live while building this):
#   - the summary header and its side panel: .cardMainInfo__title / .cardMainInfo__content
#   - everything below it: .section__title / .section__info
# Deliberately NOT scoped to a specific wrapping container class (e.g.
# ".cardMainInfo__section"): the "Начальная цена" (budget) label/value pair
# is wrapped in a plain "<div class='price'>" instead, with no
# "cardMainInfo__section" class at all - also verified live. Scanning by
# title class and then looking at the title element's OWN immediate parent
# for the matching value class works regardless of what that parent's
# class happens to be, so it isn't tripped up by this kind of
# inconsistency in the site's own markup.

_DETAIL_LABEL_VALUE_CLASS_PAIRS = (
    (".cardMainInfo__title", ".cardMainInfo__content"),
    (".section__title", ".section__info"),
)


def _detail_field_by_label(soup, label_keyword: str) -> str | None:
    keyword_lower = label_keyword.lower()
    for title_selector, value_selector in _DETAIL_LABEL_VALUE_CLASS_PAIRS:
        for title_el in soup.select(title_selector):
            label = _text_or_none(title_el)
            if not label or keyword_lower not in label.lower():
                continue
            parent = title_el.parent
            if parent is None:
                continue
            value_el = parent.select_one(value_selector)
            if value_el is not None:
                return _text_or_none(value_el)
    return None


def _extract_law_type(soup) -> str | None:
    """The law type ("44-ФЗ"/"223-ФЗ") is the summary header's own direct
    text, with a nested <span> (the purchase method name) immediately
    after it - .stripped_strings yields both in document order, so the
    FIRST one is exactly the law type, without needing to strip out the
    nested span's text some other way."""
    title_el = soup.select_one(".cardMainInfo__title")
    if title_el is None:
        return None
    for text in title_el.stripped_strings:
        return text
    return None


def parse_money(text: str | None) -> float | None:
    """Pure: "8 398 003,33 ₽" (or with a non-breaking space, or plain
    "8398003.33") -> 8398003.33. None for anything that doesn't reduce to
    a parseable number."""
    if not text:
        return None
    cleaned = text.replace("\xa0", "").strip()
    cleaned = re.sub(r"[^0-9,.]", "", cleaned)
    if not cleaned:
        return None
    cleaned = cleaned.replace(",", ".")
    # Guard against multiple '.' after the replace above (e.g. a thousands
    # separator that was itself a '.') - keep only the last one as the
    # decimal point.
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_purchase_card(html: str) -> dict:
    """Pure: parses ONE purchase's detail/card page (fetched separately
    via ZakupkiClient.fetch_purchase_details) into the fields the
    search-results page doesn't carry. Every field is independently
    try/except-guarded - a missing/malformed field never crashes the
    whole card, and this never raises even for empty/garbage input.

    NOTE (verified live while building this): a customer's own ИНН is
    often simply NOT present on this page for standard 44-FZ notices -
    the only "ИНН" label observed in testing belonged to the Federal
    Treasury's payment routing details, a different entity entirely, and
    is deliberately NOT what this looks for. `customer_inn` coming back
    None is expected and correct in that case, not a parsing bug.
    """
    result = {
        "subject": None,
        "budget": None,
        "deadline": None,
        "law_type": None,
        "customer_phone": None,
        "customer_inn": None,
        "region": None,
    }
    if not html:
        return result

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log.warning("zakupki.detail_page_parse_failed", error=str(exc))
        return result

    result["subject"] = _safe_field(soup, lambda s: _detail_field_by_label(s, "объект закупки"), "subject")
    result["deadline"] = _safe_field(
        soup, lambda s: _detail_field_by_label(s, "окончание подачи"), "deadline"
    )
    result["customer_phone"] = _safe_field(
        soup, lambda s: _detail_field_by_label(s, "контактного телефона"), "customer_phone"
    )
    result["customer_inn"] = _safe_field(soup, lambda s: _detail_field_by_label(s, "инн заказчика"), "customer_inn")
    result["region"] = _safe_field(soup, lambda s: _detail_field_by_label(s, "регион"), "region")
    result["law_type"] = _safe_field(soup, _extract_law_type, "law_type")

    raw_price = _safe_field(soup, lambda s: _detail_field_by_label(s, "начальная цена"), "budget")
    result["budget"] = parse_money(raw_price)

    return result
