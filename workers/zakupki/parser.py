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


# --- 223-FZ detail page: a COMPLETELY DIFFERENT template -------------------
#
# Verified live (2026-08-29) against three real 223-FZ notices
# (regNumber 32616263956 / 32616292415 / 32616327624). A 223-FZ notice link
# (https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html)
# 302-redirects to .../epz/order/notice/notice223/common-info.html, which
# shares NOTHING with the 44-FZ card template - measured on real pages of
# both kinds:
#
#                                 44-FZ page   223-FZ page
#   .cardMainInfo__title               8            0
#   .section__title                   45+           0
#   .common-text__title                0           29+
#   .registry-entry__header-top__title 0            1
#   .price-block__value                0            1
#
# So the two branches have zero selector overlap, which is what makes
# markup-based dispatch reliable rather than a guess.
#
# The 223 template pairs label and value as SIBLINGS
# (<div class="common-text__title">Label</div>
#  <div class="common-text__value">Value</div>) inside a shared wrapper,
# instead of 44-FZ's "value lives somewhere under the title's parent".
_223_URL_MARKERS = ("/223/", "notice223")

_COMMON_TEXT_VALUE_CLASS = "common-text__value"

# Label wording also differs between the two templates - same field, different
# words - which is why each branch carries its own label keywords rather than
# sharing one list:
#   44-FZ  "Объект закупки"                 223-FZ  "Наименование закупки"
#   44-FZ  "Окончание подачи заявок"        223-FZ  "Дата и время окончания срока подачи заявок ..."
#   44-FZ  "Номер контактного телефона"     223-FZ  "Контактный телефон"
_223_SUBJECT_LABEL = "наименование закупки"
_223_DEADLINE_LABEL = "окончания срока подачи"
_223_PHONE_LABEL = "контактный телефон"


def _looks_like_223(soup, url: str | None) -> bool:
    """URL first (authoritative and cheap - the notice lives under a /223/
    or /notice223/ path), then markup sniffing so this still dispatches
    correctly when called with HTML alone (e.g. from tests, or if the
    caller didn't keep the URL around)."""
    if url and any(marker in url for marker in _223_URL_MARKERS):
        return True
    header = soup.select_one(".registry-entry__header-top__title")
    if header is not None and "223" in (header.get_text(strip=True) or ""):
        return True
    # Last resort: the 223 template's own label class, which - measured on
    # real pages, see the table above - never appears on a 44-FZ card.
    return soup.select_one(".common-text__title") is not None


def _common_text_field_by_label(soup, label_keyword: str) -> str | None:
    """223-FZ label/value lookup: find a .common-text__title whose text
    contains `label_keyword`, then take its next SIBLING carrying
    .common-text__value. Sibling-based (not parent-scoped) on purpose -
    see _extract_223_customer_inn for the case that makes the difference."""
    keyword_lower = label_keyword.lower()
    for title_el in soup.select(".common-text__title"):
        label = _text_or_none(title_el)
        if not label or keyword_lower not in label.lower():
            continue
        value_el = title_el.find_next_sibling(class_=_COMMON_TEXT_VALUE_CLASS)
        if value_el is not None:
            return _text_or_none(value_el)
    return None


def _extract_223_law_type(soup) -> str | None:
    """On 223 pages the law type sits in the page's own header title,
    inline with the purchase method as ONE string ("223-ФЗ Аукцион в
    электронной форме, ..."), rather than 44-FZ's title-plus-nested-span -
    so this pulls the law token out by pattern instead of by position."""
    header = soup.select_one(".registry-entry__header-top__title")
    if header is None:
        return None
    text = header.get_text(" ", strip=True)
    match = re.search(r"\d{2,3}\s*-\s*ФЗ", text or "")
    return match.group(0).replace(" ", "") if match else None


def _extract_223_customer_inn(soup) -> str | None:
    """Unlike 44-FZ (where the customer's own ИНН is usually absent
    entirely), the 223 template DOES carry it, in the "Сведения о
    заказчике" block, as a grey "ИНН" label followed by its value:

        <div class="common-text__value common-text__value--gray">ИНН</div>
        <div class="ml-1 common-text__value">6731002565</div>

    Note the label itself also carries .common-text__value - so a
    parent-scoped ".common-text__value" lookup would return the string
    "ИНН" rather than the number. Matching the grey label and stepping to
    its next sibling is what avoids that.

    Only one ИНН was present on each real page tested, and it belonged to
    the заказчик - but the label is matched exactly (not by substring) so
    a future "ИНН поставщика"-style addition can't be mistaken for it.
    """
    for label_el in soup.select(".common-text__value--gray"):
        label = (_text_or_none(label_el) or "").rstrip(":").strip().lower()
        if label != "инн":
            continue
        value_el = label_el.find_next_sibling(class_=_COMMON_TEXT_VALUE_CLASS)
        digits = re.sub(r"\D", "", _text_or_none(value_el) or "") if value_el is not None else ""
        return digits or None
    return None


def _parse_223_card(soup, result: dict, url: str | None) -> dict:
    result["subject"] = _safe_field(
        soup, lambda s: _common_text_field_by_label(s, _223_SUBJECT_LABEL), "subject"
    )
    result["deadline"] = _safe_field(
        soup, lambda s: _common_text_field_by_label(s, _223_DEADLINE_LABEL), "deadline"
    )
    result["customer_phone"] = _safe_field(
        soup, lambda s: _common_text_field_by_label(s, _223_PHONE_LABEL), "customer_phone"
    )
    result["customer_inn"] = _safe_field(soup, _extract_223_customer_inn, "customer_inn")

    law_type = _safe_field(soup, _extract_223_law_type, "law_type")
    if law_type is None and url and any(marker in url for marker in _223_URL_MARKERS):
        # Not a guess: the /223/ URL namespace IS the law type. This only
        # matters if the site restyles the header element out from under us.
        law_type = "223-ФЗ"
    result["law_type"] = law_type

    # The budget is the one field the two templates happen to share a class
    # for (.price-block__value, the same one the SEARCH page uses) - which is
    # exactly why budget kept working on 223 notices while everything else
    # came back None before this branch existed.
    raw_price = _safe_field(soup, lambda s: _text_or_none(s.select_one(".price-block__value")), "budget")
    result["budget"] = parse_money(raw_price)

    # `region` stays None: the 223 template genuinely has no "Регион" field.
    # The closest thing is "Место нахождения", a full postal address
    # ("214020, СМОЛЕНСКАЯ ОБЛАСТЬ, Г.. СМОЛЕНСК, УЛ. ШЕВЧЕНКО, Д. 77А") -
    # mapper.py feeds `region` straight into the lead's `city`, and shoving a
    # whole address in there would be worse than leaving it empty. Documented
    # in README.md rather than papered over.
    return result


def _parse_44_card(soup, result: dict) -> dict:
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


def parse_purchase_card(html: str, url: str | None = None) -> dict:
    """Pure: parses ONE purchase's detail/card page (fetched separately
    via ZakupkiClient.fetch_purchase_details) into the fields the
    search-results page doesn't carry, picking the 44-FZ or the 223-FZ
    branch automatically (see _looks_like_223). Passing `url` makes that
    choice authoritative; without it, dispatch falls back to markup
    sniffing. Every field is independently try/except-guarded - a
    missing/malformed field never crashes the whole card, and this never
    raises even for empty/garbage input.

    Both branches return the same seven keys, so callers (mapper.py) never
    need to know which law a notice came under. What differs is which of
    them can actually be filled - see README.md "Что реально есть на
    странице" for the honest per-law field table:

      - 44-FZ: `customer_inn` is usually absent (the only "ИНН" label
        observed in live testing belonged to the Federal Treasury's payment
        routing details, a different entity entirely, and is deliberately
        NOT picked up here).
      - 223-FZ: `customer_inn` IS present, but `region` genuinely is not.
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

    try:
        is_223 = _looks_like_223(soup, url)
    except Exception as exc:  # noqa: BLE001 - never let dispatch itself crash a card
        log.warning("zakupki.detail_law_type_detection_failed", error=str(exc))
        is_223 = False

    if is_223:
        return _parse_223_card(soup, result, url)
    return _parse_44_card(soup, result)
