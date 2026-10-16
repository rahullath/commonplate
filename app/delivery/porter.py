"""Porter — the delivery partner (Oct 2026 decision; Borzo/Shiprocket retired).

Two modes, one interface:

- **Manual (default, works today).** Porter's merchant API is gated behind
  their enterprise team (our sign-up was geo-restricted, see
  docs/COURIER_ROUTER.md), so the kitchen books each rider in the Porter app.
  This module turns an order into a ready-to-paste booking card (pickup,
  drop, Maps pin, customer phone, cash to collect), which is sent to Telegram
  when the POS marks the order food-ready and shown in the admin panel. the owner
  pastes the Porter tracking link back and the customer gets it.
- **API (later).** When Porter issues an API key, implement `quote()`/`book()`
  below and set PORTER_API_KEY; `orders.dispatch_rider()` already calls
  `book()` first when `api_configured()` is true, so nothing else changes.

Uber Direct was evaluated too: in India it runs only as an ONDC logistics
provider (launched Bengaluru, Dec 2025), with no merchant API and no city-specific
service yet. Revisit if that changes.
"""
from ..business import business
import os

from .config import PICKUP_ADDRESS, PICKUP_LAT, PICKUP_LNG, PICKUP_PHONE

PORTER_API_KEY = os.environ.get("PORTER_API_KEY", "")
PORTER_APP_URL = "https://porter.in/"


class PorterNotConfigured(Exception):
    """Raised by the API path until Porter issues credentials."""


def api_configured() -> bool:
    return bool(PORTER_API_KEY)


def _maps_link(lat, lng) -> str:
    return f"https://maps.google.com/?q={lat},{lng}" if lat and lng else ""


def booking_details(order: dict) -> dict:
    """Everything the Porter app asks for, in one place."""
    is_cod = (order.get("payment_method") or "").lower() == "cod"
    cod = float(order.get("total") or 0) if is_cod else 0.0
    items = order.get("items") or []
    return {
        "order_id": order["id"],
        "pickup_name": business.name,
        "pickup_address": PICKUP_ADDRESS,
        "pickup_phone": PICKUP_PHONE,
        "pickup_map": _maps_link(PICKUP_LAT, PICKUP_LNG),
        "drop_name": order.get("customer_name") or "Customer",
        "drop_address": order.get("delivery_address") or "",
        "drop_landmark": order.get("delivery_landmark") or "",
        "drop_phone": order.get("customer_phone") or "",
        "drop_map": _maps_link(order.get("delivery_lat"), order.get("delivery_lng")),
        "cash_to_collect": round(cod, 2),
        # UPI the kitchen hasn't seen yet: not "prepaid" until it's confirmed.
        "upi_unconfirmed": (not is_cod) and not order.get("paid"),
        "item_count": sum(int(i.get("qty") or 1) for i in items),
        "porter_url": PORTER_APP_URL,
    }


def booking_text(order: dict) -> str:
    """Plain-text booking card: paste-ready for the Porter app / Telegram."""
    d = booking_details(order)
    lines = [
        f"PORTER — Order #{d['order_id']} ({d['item_count']} item{'' if d['item_count'] == 1 else 's'}, food parcel, 2-wheeler)",
        "",
        "PICKUP",
        f"{d['pickup_name']}, {d['pickup_address']}",
        f"Phone: {d['pickup_phone']}",
        "",
        "DROP",
        d["drop_name"],
        d["drop_address"] + (f" (landmark: {d['drop_landmark']})" if d["drop_landmark"] else ""),
        f"Phone: {d['drop_phone']}",
    ]
    if d["drop_map"]:
        lines.append(f"Pin: {d['drop_map']}")
    if d["cash_to_collect"]:
        money = f"COLLECT CASH: ₹{d['cash_to_collect']:.0f}"
    elif d["upi_unconfirmed"]:
        money = f"UPI ₹{float(order.get('total') or 0):.0f} NOT CONFIRMED YET: check before the rider leaves"
    else:
        money = "PAID BY UPI: collect nothing"
    lines += ["", money]
    return "\n".join(lines)


def quote(order: dict) -> dict:
    """Porter API fare quote. Not available until Porter issues a key."""
    raise PorterNotConfigured("Porter API access not granted yet; book in the Porter app")


def book(order: dict) -> dict:
    """Porter API booking -> {"tracking_url", "courier_name", "porter_order_id"}."""
    raise PorterNotConfigured("Porter API access not granted yet; book in the Porter app")
