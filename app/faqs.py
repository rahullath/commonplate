"""Data-driven FAQ answers.

Every answer is generated from live config / menu data (delivery zones, fees,
minimums, opening hours, real menu prices) so the visible FAQ and the
FAQPage JSON-LD can never drift from what the site actually does.
"""
from .business import business
from .config import MONDAY_OPENS_AT, OPENING_HOURS, WHATSAPP_ORDER_DISPLAY


def _free_clause(free_above) -> str:
    """The free-delivery promo is off by default; only mention it when it is on."""
    from .config import FREE_DELIVERY_ENABLED
    if not FREE_DELIVERY_ENABLED:
        return ""
    return f" Delivery is free on orders over ₹{free_above}."


def _hhmm_to_12h(hhmm: str) -> str:
    h, m = hhmm.split(":")
    h = int(h)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m} {suffix}".replace(":00 ", " ")


def _open_text() -> str:
    wk_open, wk_close = OPENING_HOURS["mon_sat"]
    su_open, su_close = OPENING_HOURS["sunday"]
    return (
        f"Monday to Saturday {_hhmm_to_12h(wk_open)} to {_hhmm_to_12h(wk_close)}, "
        f"Sunday {_hhmm_to_12h(su_open)} to {_hhmm_to_12h(su_close)}"
    )


def _min_order_text(zones) -> str:
    if len({z["min_order"] for z in zones}) == 1:
        return f"₹{zones[0]['min_order']} of food, wherever you are in our area"
    return ", ".join(f"₹{z['min_order']} within {z['max_km']:.0f} km" for z in zones)


def _fee_text(zones) -> str:
    return ", ".join(f"₹{z['fee']} up to {z['max_km']:.0f} km" for z in zones)


def _areas_clause() -> str:
    """' That covers A, B and C.' when service areas are configured, else ''."""
    areas = business.service_areas
    if not areas:
        return ""
    names = areas[0] if len(areas) == 1 else ", ".join(areas[:-1]) + " and " + areas[-1]
    return f" That covers {names}."


def delivery_faqs(zones, free_above) -> list[dict]:
    """Delivery-page FAQs."""
    zones = sorted(zones, key=lambda z: z["max_km"])
    return [
        {
            "q": f"How much does {business.name} delivery cost?",
            "a": (
                f"A fixed fee by distance: {_fee_text(zones)} from the {business.locality} "
                "kitchen. It is shown at checkout and included in your total, "
                f"and nothing is added later.{_free_clause(free_above)}"
            ),
        },
        {
            "q": f"Does {business.name} deliver to my area?",
            "a": (
                f"{business.name} delivers anywhere within {zones[-1]['max_km']:.0f} km of "
                f"the kitchen in {business.locality}.{_areas_clause()} Farther than that "
                f"is pickup in {business.locality}."
            ),
        },
        {
            "q": f"How do I order ahead at {business.name}?",
            "a": (
                "Pick a lunch or dinner window up to two weeks ahead and the food is "
                "cooked for it. An hour's notice means it reaches you hot."
            ),
        },
        {
            "q": f"How can I pay at {business.name}?",
            "a": (
                "UPI when you order, or cash on delivery. The total at checkout "
                "already includes delivery, so that is all you pay."
            ),
        },
        {
            "q": f"Is there a minimum order for delivery at {business.name}?",
            "a": (
                f"Yes: {_min_order_text(zones)}. The checkout shows what your "
                "area needs before you submit."
            ),
        },
    ]


def landing_faqs(zones, free_above, price_range, has_upi) -> list[dict]:
    """Homepage FAQs."""
    zones = sorted(zones, key=lambda z: z["max_km"])
    repay = "UPI at checkout, or cash on delivery" if has_upi else "cash or by message"
    order_by = (
        f"Add dishes from the menu on this site and check out, or message us on WhatsApp at {WHATSAPP_ORDER_DISPLAY}."
        if WHATSAPP_ORDER_DISPLAY else "Add dishes from the menu on this site and check out."
    )
    return [
        {
            "q": f"What are {business.name}'s opening hours?",
            "a": (
                f"{_open_text()}. Monday is a half-day for online orders until "
                f"{_hhmm_to_12h(MONDAY_OPENS_AT)}."
            ),
        },
        {
            "q": f"Does {business.name} deliver in {business.city}, and what does it cost?",
            "a": (
                f"Yes, anywhere within {zones[-1]['max_km']:g} km of the kitchen in {business.locality}."
                f"{_areas_clause()} Delivery is a fixed fee by distance ({_fee_text(zones)}), "
                f"included in the total you see at checkout.{_free_clause(free_above)}"
            ),
        },
        {
            "q": f"How much does food at {business.name} cost?",
            "a": f"Menu prices run from about ₹{price_range[0]} to ₹{price_range[1]}.",
        },
        {
            "q": f"How do I order from {business.name}?",
            "a": f"{order_by} Pay {repay}. Updates and a tracking link come by message.",
        },
    ]
