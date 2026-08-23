"""Pure mapping: one parsed purchase dict (from parser.py) -> one
RawCompanyRequest for the core, or None if there's no customer name to
lead with. No I/O, no network - trivial to unit test on hand-built
fixture dicts.
"""

from .core_client import RawCompanyRequest


def map_purchase_to_lead(purchase: dict, keyword: str) -> RawCompanyRequest | None:
    customer_name = purchase.get("customer_name")
    if not customer_name:
        return None

    return RawCompanyRequest(
        name=customer_name,
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
            "budget": purchase.get("price"),
            "subject": purchase.get("subject"),
            "inn": purchase.get("inn"),
            "keyword": keyword,
            "reg_number": purchase.get("reg_number"),
            # The budget (НМЦК) is real and already approved - unlike a
            # casually mentioned figure in a chat message, this is exactly
            # what the core's budgetMentioned rule (+20) is meant to reward.
            "budgetMentioned": True,
        },
    )
