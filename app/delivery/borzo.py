"""Borzo (WeFast) hyperlocal delivery API client.

Handles order creation, cancellation, tracking, and webhook verification.
Unlike Shiprocket, Borzo is a single API call to dispatch — no separate
serviceability, AWB assignment, or pickup scheduling steps.
"""
from ..business import business
import hashlib
import hmac
import json
import logging

import httpx

from .config import BORZO_AUTH_TOKEN, BORZO_BASE_URL, PICKUP_ADDRESS, PICKUP_LAT, PICKUP_LNG, PICKUP_PHONE

log = logging.getLogger("borzo")


class BorzoError(Exception):
    """Raised when a Borzo API call fails. Carries full request/response for debugging."""

    def __init__(self, step: str, payload: dict, response: dict):
        self.step = step
        self.payload = payload
        self.response = response
        super().__init__(f"{step} failed: {response}")


def _headers() -> dict:
    return {"X-DV-Auth-Token": BORZO_AUTH_TOKEN, "Content-Type": "application/json"}


def _format_items_as_matter(items: list[dict]) -> str:
    """Convert order items to a delivery description string for the courier."""
    parts = []
    for it in items:
        qty = it.get("qty", 1)
        name = it.get("name", "item")
        parts.append(f"{qty}x {name}")
    return f"{business.name}: " + ", ".join(parts)


def calculate_order(
    delivery_address: str,
    items: list[dict],
    delivery_lat: str | None = None,
    delivery_lng: str | None = None,
    total: float = 0,
    cod_amount: float = 0,
    note: str | None = None,
) -> dict:
    """Get price estimate before creating an order."""
    matter = _format_items_as_matter(items)
    if total:
        matter += f" (₹{total:.0f})"

    points = [
        {
            "address": PICKUP_ADDRESS,
            "contact_person": {"phone": PICKUP_PHONE, "name": business.name},
            "latitude": str(PICKUP_LAT),
            "longitude": str(PICKUP_LNG),
        },
        {
            "address": delivery_address,
            "contact_person": {"phone": "", "name": "Customer"},
            "latitude": str(delivery_lat) if delivery_lat else None,
            "longitude": str(delivery_lng) if delivery_lng else None,
            "note": note,
        },
    ]

    payload = {
        "matter": matter,
        "vehicle_type_id": 8,  # motorbike
        "total_weight_kg": 2,
        "is_thermobox_required": True,
        "points": points,
    }

    with httpx.Client(timeout=20) as c:
        r = c.post(f"{BORZO_BASE_URL}/calculate-order", headers=_headers(), json=payload)
        data = r.json()
        if not data.get("is_successful"):
            raise BorzoError("calculate-order", payload=payload, response=data)
        return data.get("order", {})


def create_order(
    order_id: int,
    customer_name: str,
    customer_phone: str,
    delivery_address: str,
    items: list[dict],
    total: float,
    payment_method: str = "cod",
    cod_amount: float = 0,
    delivery_lat: str | None = None,
    delivery_lng: str | None = None,
    note: str | None = None,
) -> dict:
    """Create and dispatch a delivery order in one call.

    Returns {borzo_order_id, order_name, tracking_url, payment_amount}.
    """
    matter = _format_items_as_matter(items)
    if total:
        matter += f" (₹{total:.0f})"

    delivery_phone = customer_phone if customer_phone.startswith("91") else f"91{customer_phone}"

    # Ensure address is Google-geocodable — append city/state/pincode if missing
    addr = delivery_address
    if business.city.lower() not in addr.lower():
        addr = f"{addr}, {business.city}, {business.state}, {business.country}"

    points = [
        {
            "address": PICKUP_ADDRESS,
            "contact_person": {"phone": PICKUP_PHONE, "name": business.name},
            "latitude": str(PICKUP_LAT),
            "longitude": str(PICKUP_LNG),
            "note": "Pick up from restaurant counter",
        },
        {
            "address": addr,
            "contact_person": {"phone": delivery_phone, "name": customer_name},
            "client_order_id": str(order_id),
            "latitude": str(delivery_lat) if delivery_lat else None,
            "longitude": str(delivery_lng) if delivery_lng else None,
            "is_order_payment_here": False,
        },
    ]

    payment = "non_cash"  # company account pays from Borzo balance (see client-profile payment_methods)

    payload = {
        "matter": matter,
        "vehicle_type_id": 8,  # motorbike (up to 20kg)
        "total_weight_kg": 2,
        "is_contact_person_notification_enabled": True,
        "is_thermobox_required": True,  # keep food hot
        "payment_method": payment,
        "points": points,
    }

    with httpx.Client(timeout=20) as c:
        r = c.post(f"{BORZO_BASE_URL}/create-order", headers=_headers(), json=payload)
        data = r.json()
        if not data.get("is_successful"):
            raise BorzoError("create-order", payload=payload, response=data)

    order = data.get("order", {})
    borzo_id = order.get("order_id")
    order_name = order.get("order_name", "")
    tracking_url = order.get("tracking_url", f"https://borzodelivery.com/in/order/{borzo_id}")
    log.info("Borzo order created: %s (%s)", borzo_id, order_name)

    return {
        "sr_order_id": borzo_id,       # reuse DB column name for compatibility
        "sr_awb": order_name,           # reuse DB column name
        "sr_courier": "Borzo",
        "sr_tracking_url": tracking_url,
        "_borzo_order": order,
    }


def cancel_order(borzo_order_id: int) -> dict:
    """Cancel a Borzo order."""
    with httpx.Client(timeout=20) as c:
        r = c.post(
            f"{BORZO_BASE_URL}/cancel-order",
            headers=_headers(),
            json={"order_id": borzo_order_id},
        )
        data = r.json()
        if not data.get("is_successful"):
            raise BorzoError("cancel-order", {"order_id": borzo_order_id}, data)
    log.info("Borzo order cancelled: %s", borzo_order_id)
    return data


def get_order(borzo_order_id: int) -> dict:
    """Get order details and status."""
    with httpx.Client(timeout=20) as c:
        r = c.get(
            f"{BORZO_BASE_URL}/orders",
            headers=_headers(),
            params={"order_id": borzo_order_id},
        )
        data = r.json()
        if not data.get("is_successful"):
            raise BorzoError("get-order", {"order_id": borzo_order_id}, data)
    return data.get("order", {})


def list_orders() -> list[dict]:
    """List all orders."""
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{BORZO_BASE_URL}/orders", headers=_headers())
        data = r.json()
        if not data.get("is_successful"):
            raise BorzoError("list-orders", {}, data)
    return data.get("orders", [])


def get_courier_location(borzo_order_id: int) -> dict:
    """Get live courier location for an active order."""
    with httpx.Client(timeout=20) as c:
        r = c.get(
            f"{BORZO_BASE_URL}/courier/location",
            headers=_headers(),
            params={"order_id": borzo_order_id},
        )
        data = r.json()
        if not data.get("is_successful"):
            raise BorzoError("courier-location", {"order_id": borzo_order_id}, data)
    return data.get("courier", {})


def verify_webhook_signature(body: bytes, signature: str, callback_token: str) -> bool:
    """Verify that a webhook request is authentic using HMAC SHA256."""
    expected = hmac.new(
        callback_token.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
