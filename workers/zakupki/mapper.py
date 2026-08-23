"""Pure mapping: a parsed purchase dict (from parser.py's
parse_search_html), optionally enriched with that purchase's own detail
page (parse_purchase_card), -> one RawCompanyRequest for the core, or None
if there's no customer name to lead with. No I/O, no network - trivial to
unit test on hand-built fixture dicts.
"""

from .core_client import RawCompanyRequest
from .parser import parse_money


def merge_purchase_with_details(purchase: dict, details: dict | None) -> dict:
    """Combines a search-results purchase dict with its own detail-page
    dict (or None, if details were never fetched - see runner.py
    FETCH_DETAILS/MAX_DETAILS) into one dict ready for map_purchase_to_lead.

    budget is always produced as a number here (parse_money), regardless
    of whether the detail page was fetched: falls back to parsing the
    search-results page's raw price text when there's no detail-page
    budget to prefer.
    """
    merged = dict(purchase)

    if details:
        merged["subject"] = details.get("subject") or purchase.get("subject")
        merged["region"] = purchase.get("region") or details.get("region")
        merged["deadline"] = details.get("deadline")
        merged["law_type"] = details.get("law_type")
        merged["customer_inn"] = details.get("customer_inn") or purchase.get("inn")
        merged["customer_phone"] = details.get("customer_phone")
        detail_budget = details.get("budget")
        merged["budget"] = detail_budget if detail_budget is not None else parse_money(purchase.get("price"))
    else:
        merged["budget"] = parse_money(purchase.get("price"))
        merged["deadline"] = None
        merged["law_type"] = None
        merged["customer_inn"] = purchase.get("inn")
        merged["customer_phone"] = None

    return merged


def map_purchase_to_lead(purchase: dict, keyword: str) -> RawCompanyRequest | None:
    customer_name = purchase.get("customer_name")
    if not customer_name:
        return None

    return RawCompanyRequest(
        name=customer_name,
        # A contact phone straight off the notice's own "Контактная
        # информация" block, when the detail page had one - lets the
        # core's contact+geo (+10) and phone-type (PhoneUtil) rules fire.
        phone=purchase.get("customer_phone"),
        city=purchase.get("region"),
        source="ZAKUPKI",
        source_url=purchase.get("detail_url"),
        # The customer is a real government/municipal body or state-owned
        # company - it almost certainly already has a website. Reporting
        # hasSite=false here would trigger the core's "+40 no site" rule
        # falsely; the real intent signal is the approved budget + the
        # purchase notice itself (see budgetMentioned below).
        has_site=True,
        raw={
            "budget": purchase.get("budget"),
            "subject": purchase.get("subject"),
            "inn": purchase.get("customer_inn") or purchase.get("inn"),
            "keyword": keyword,
            "reg_number": purchase.get("reg_number"),
            "deadline": purchase.get("deadline"),
            "law_type": purchase.get("law_type"),
            # The budget (НМЦК) is real and already approved - unlike a
            # casually mentioned figure in a chat message, this is exactly
            # what the core's budgetMentioned rule (+20) is meant to reward.
            "budgetMentioned": True,
        },
    )
