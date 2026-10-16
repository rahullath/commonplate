"""Shiprocket Quick (hyperlocal) API client.

Handles auth, serviceability checks, order creation, dispatch, and tracking.
Auth token is cached in SQLite and auto-refreshed on expiry.
"""
from ..business import business
import json
import logging
from datetime import datetime, timedelta

import httpx

from ..config import DB_FILE
from .config import (
    DEFAULT_WEIGHT_KG,
    PICKUP_ADDRESS,
    PICKUP_LAT,
    PICKUP_LNG,
    PICKUP_PHONE,
    PICKUP_PINCODE,
    SHIPROCKET_API_EMAIL,
    SHIPROCKET_API_PASSWORD,
    SHIPROCKET_BASE_URL,
    SHIPROCKET_CHANNEL_ID,
)

log = logging.getLogger("shiprocket")


class DispatchError(Exception):
    """Raised when a Shiprocket API call fails. Carries full request/response for debugging."""

    def __init__(self, step: str, payload: dict, response: dict):
        self.step = step
        self.payload = payload
        self.response = response
        super().__init__(f"{step} failed: {response}")


def _raise_for_status(r: httpx.Response) -> None:
    """Like r.raise_for_status(), but includes Shiprocket's response body in the error."""
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise httpx.HTTPStatusError(f"{e}: {r.text}", request=e.request, response=e.response) from None


_TOKEN_TABLE = """
CREATE TABLE IF NOT EXISTS shiprocket_tokens (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def _conn():
    import sqlite3
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_token_table():
    conn = _conn()
    conn.executescript(_TOKEN_TABLE)
    conn.commit()
    conn.close()


def _get_cached_token() -> str | None:
    _ensure_token_table()
    conn = _conn()
    row = conn.execute("SELECT token, expires_at FROM shiprocket_tokens WHERE id=1").fetchone()
    conn.close()
    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) > datetime.utcnow():
        return row["token"]
    return None


def _cache_token(token: str, expires_in_hours: int = 230) -> None:
    _ensure_token_table()
    expires_at = (datetime.utcnow() + timedelta(hours=expires_in_hours)).isoformat()
    conn = _conn()
    conn.execute(
        "INSERT INTO shiprocket_tokens(id, token, expires_at) VALUES(1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET token=excluded.token, expires_at=excluded.expires_at",
        (token, expires_at),
    )
    conn.commit()
    conn.close()


def get_token() -> str:
    """Get a valid auth token, refreshing if needed."""
    cached = _get_cached_token()
    if cached:
        return cached
    if not SHIPROCKET_API_EMAIL or not SHIPROCKET_API_PASSWORD:
        raise RuntimeError("SHIPROCKET_API_EMAIL and SHIPROCKET_API_PASSWORD must be set in .env")
    with httpx.Client(timeout=20) as c:
        r = c.post(
            f"{SHIPROCKET_BASE_URL}/auth/login",
            json={"email": SHIPROCKET_API_EMAIL, "password": SHIPROCKET_API_PASSWORD},
        )
        _raise_for_status(r)
        data = r.json()
    token = data.get("token")
    if not token:
        raise RuntimeError(f"Shiprocket auth failed: {data}")
    _cache_token(token)
    log.info("Shiprocket auth token refreshed")
    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


def check_serviceability(
    delivery_pincode: str,
    delivery_lat: str | None = None,
    delivery_lng: str | None = None,
    weight_kg: float = DEFAULT_WEIGHT_KG,
) -> list[dict]:
    """Check which hyperlocal (Quick) couriers serve pickup→delivery.

    Hyperlocal serviceability requires lat/long on both ends — pass them if
    already known, otherwise this geocodes the delivery pincode itself.

    Returns list of courier dicts with: courier_name, courier_company_id,
    estimated_delivery_days, freight_charge, rating, etc.
    """
    if not delivery_lat or not delivery_lng:
        delivery_lat, delivery_lng = geocode_address("", delivery_pincode)

    with httpx.Client(timeout=20) as c:
        r = c.get(
            f"{SHIPROCKET_BASE_URL}/courier/serviceability",
            headers=_headers(),
            params={
                "pickup_postcode": PICKUP_PINCODE,
                "delivery_postcode": delivery_pincode,
                "weight": str(weight_kg),
                "lat_from": PICKUP_LAT,
                "long_from": PICKUP_LNG,
                "lat_to": delivery_lat,
                "long_to": delivery_lng,
                "is_new_hyperlocal": 1,
                "only_local": 1,  # filter to hyperlocal couriers only
            },
        )
        _raise_for_status(r)
        data = r.json()

    couriers = data.get("data", {}).get("available_courier_companies", [])
    return [
        {
            "courier_company_id": q["courier_company_id"],
            "courier_name": q["courier_name"],
            "estimated_delivery_days": q.get("estimated_delivery_days", ""),
            "freight_charge": q.get("freight_charge", 0),
            "rating": q.get("rating", 0),
            "etd": q.get("etd", ""),
            "is_hyperlocal": q.get("is_hyperlocal", False),
        }
        for q in couriers
    ]


def geocode_address(address: str, pincode: str) -> tuple[str, str] | tuple[None, None]:
    """Best-effort geocode of a delivery address to (lat, lng) strings via OSM Nominatim.

    Falls back to geocoding just the pincode if the full address isn't found.
    Returns (None, None) if geocoding fails entirely — caller must handle that.
    """
    headers = {"User-Agent": f"{business.name} ordering/1.0"}
    place = f"{business.city}, {business.state}, {business.country}"
    queries = [f"{address}, {pincode}, {place}", f"{pincode}, {place}"]
    with httpx.Client(timeout=10) as c:
        for q in queries:
            try:
                r = c.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": q, "format": "json", "countrycodes": "in", "limit": 1},
                    headers=headers,
                )
                r.raise_for_status()
                results = r.json()
                if results:
                    return results[0]["lat"], results[0]["lon"]
            except Exception as e:
                log.warning("Geocoding failed for %r: %s", q, e)
    return None, None


def create_order(
    order_id: int,
    customer_name: str,
    customer_phone: str,
    delivery_address: str,
    delivery_pincode: str,
    items: list[dict],
    total: float,
    payment_method: str = "cod",
    cod_amount: float = 0,
    delivery_lat: str | None = None,
    delivery_lng: str | None = None,
) -> dict:
    """Create a hyperlocal (Shiprocket Quick) order. Returns {sr_order_id, shipment_id}.

    Items: [{"name": str, "sku": str, "units": int, "selling_price": float}]
    """
    order_items = []
    for it in items:
        order_items.append({
            "name": it["name"],
            "sku": it.get("sku", it["name"][:20]),
            "units": it.get("qty", 1),
            "selling_price": it["price"],
            "hsn": "2106",  # food preparations n.e.s.
            "category_name": "Food",
        })

    # Use customer-provided GPS coords if available, otherwise geocode
    lat, lng = delivery_lat, delivery_lng
    if not lat or not lng:
        lat, lng = geocode_address(delivery_address, delivery_pincode)

    payload = {
        "order_id": str(order_id),
        "order_date": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "pickup_location": "Shop",
        "shipping_method": "HL",  # hyperlocal (Shiprocket Quick)
        "billing_customer_name": customer_name[:40],
        "billing_last_name": "",
        "billing_address": delivery_address[:200],
        "billing_pincode": delivery_pincode,
        "billing_city": business.city,
        "billing_state": business.state,
        "billing_country": business.country,
        "billing_phone": customer_phone,
        "shipping_is_billing": True,
        "order_items": order_items,
        # Always prepaid — shipping fee is paid from the restaurant's wallet.
        # The customer's actual payment method (cod/upi) is handled via cod_amount.
        "payment_method": "Prepaid",
        "cod_amount": cod_amount if payment_method == "cod" else 0,
        "sub_total": total,
        "length": 25,   # cm — food parcel defaults
        "breadth": 20,
        "height": 10,
        "weight": DEFAULT_WEIGHT_KG,
    }
    if lat and lng:
        payload["latitude"] = lat
        payload["longitude"] = lng
    else:
        log.warning("Could not geocode delivery address for order %s; sending without lat/long", order_id)

    with httpx.Client(timeout=20) as c:
        r = c.post(
            f"{SHIPROCKET_BASE_URL}/orders/create/adhoc",
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 400:
            raise DispatchError(
                "create/adhoc",
                payload=payload,
                response={"status": r.status_code, "body": r.text},
            )
        data = r.json()

    return {
        "sr_order_id": data.get("order_id"),
        "shipment_id": data.get("shipment_id"),
        "_payload": payload,
        "_response": data,
    }


def assign_awb(shipment_id: int, courier_id: int | None = None) -> dict:
    """Assign a courier (AWB) to a Shiprocket shipment.

    Uses the standard documented assign/awb API params only:
      - shipment_id (required)
      - courier_id (optional) — if omitted Shiprocket auto-assigns

    Returns {awb_code, courier_name, courier_company_id}.
    """
    payload: dict = {"shipment_id": shipment_id}
    if courier_id is not None:
        payload["courier_id"] = courier_id

    with httpx.Client(timeout=20) as c:
        r = c.post(
            f"{SHIPROCKET_BASE_URL}/courier/assign/awb",
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 400:
            raise DispatchError(
                "assign/awb",
                payload=payload,
                response={"status": r.status_code, "body": r.text},
            )
        data = r.json()

    # Async response — Shiprocket returns {"success": true, "message": "We are processing your request"}
    if data.get("success") and not data.get("response"):
        log.info("AWB assignment accepted (async): %s", data)
        return {
            "awb_code": None,
            "courier_name": "",
            "courier_company_id": None,
            "_async": True,
            "_raw": data,
        }

    response = data.get("response", {})
    if response.get("data", {}).get("awb_code"):
        awb_data = response["data"]
        log.info("AWB assigned: %s (courier: %s)", awb_data["awb_code"], awb_data.get("courier_name"))
        return {
            "awb_code": awb_data["awb_code"],
            "courier_name": awb_data.get("courier_name", ""),
            "courier_company_id": awb_data.get("courier_company_id"),
        }
    raise RuntimeError(f"AWB assignment failed: {data}")


def schedule_pickup(sr_order_id: int) -> dict:
    """Schedule pickup for a dispatched order. Returns pickup confirmation."""
    with httpx.Client(timeout=20) as c:
        r = c.post(
            f"{SHIPROCKET_BASE_URL}/courier/generate/pickup",
            headers=_headers(),
            json={"order_id": sr_order_id},
        )
        _raise_for_status(r)
        data = r.json()

    log.info("Pickup scheduled for order %s: %s", sr_order_id, data)
    return data


def track_awb(awb_code: str) -> dict:
    """Track a shipment by AWB code.

    Returns {status_id, status_text, etd, current_location, courier_name, scans}.
    """
    with httpx.Client(timeout=20) as c:
        r = c.get(
            f"{SHIPROCKET_BASE_URL}/courier/track/awb/{awb_code}",
            headers=_headers(),
        )
        _raise_for_status(r)
        data = r.json()

    scans = data.get("scans", [])
    current_location = scans[0]["location"] if scans else ""
    return {
        "status_id": data.get("current_status_id"),
        "status_text": data.get("current_status", ""),
        "etd": data.get("etd", ""),
        "current_location": current_location,
        "courier_name": data.get("courier_name", ""),
        "scans": scans,
    }


def track_order(order_id: str, channel_id: int | None = None) -> dict:
    """Track a shipment by Shiprocket order ID.

    Uses /courier/track?order_id=X which returns a tracking_data wrapper
    with shipment_track, shipment_track_activities, track_url, etd.
    """
    params: dict = {"order_id": order_id}
    if channel_id:
        params["channel_id"] = channel_id

    with httpx.Client(timeout=20) as c:
        r = c.get(
            f"{SHIPROCKET_BASE_URL}/courier/track",
            headers=_headers(),
            params=params,
        )
        _raise_for_status(r)
        data = r.json()

    tracking = data[0].get("tracking_data", {}) if isinstance(data, list) and data else {}
    track = tracking.get("shipment_track", [{}])[0] if tracking.get("shipment_track") else {}
    activities = tracking.get("shipment_track_activities", [])
    current_location = activities[0]["location"] if activities else ""

    return {
        "status_text": track.get("current_status", ""),
        "origin": track.get("origin", ""),
        "destination": track.get("destination", ""),
        "edd": tracking.get("etd", ""),
        "track_url": tracking.get("track_url", ""),
        "current_location": current_location,
        "activities": activities,
    }


def get_pickup_addresses() -> list[dict]:
    """Return list of configured pickup locations on the account."""
    with httpx.Client(timeout=20) as c:
        r = c.get(
            f"{SHIPROCKET_BASE_URL}/settings/company/pickup",
            headers=_headers(),
        )
        _raise_for_status(r)
        data = r.json()
    return data.get("data", {}).get("recent_addresses", [])


def dispatch_order(
    order_id: int,
    customer_name: str,
    customer_phone: str,
    delivery_address: str,
    delivery_pincode: str,
    items: list[dict],
    total: float,
    payment_method: str,
    delivery_lat: str | None = None,
    delivery_lng: str | None = None,
) -> dict:
    """Full dispatch flow: create hyperlocal order → assign AWB → schedule pickup.

    Returns {sr_order_id, awb_code, courier_name, ...}.

    create_order() marks the order shipping_method="HL" so Shiprocket treats
    it as hyperlocal; AWB assignment is left to Shiprocket's auto-assign
    (manually passing courier_id triggers a "Try Assigning Courier via
    Shiprocket Quick" error on hyperlocal shipments).
    """
    sr = create_order(
        order_id=order_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        delivery_address=delivery_address,
        delivery_pincode=delivery_pincode,
        items=items,
        total=total,
        payment_method=payment_method,
        cod_amount=total if payment_method == "cod" else 0,
        delivery_lat=delivery_lat,
        delivery_lng=delivery_lng,
    )

    try:
        awb = assign_awb(sr["shipment_id"])
    except DispatchError as e:
        raise DispatchError(
            "assign/awb",
            payload={**e.payload, "create_response": sr.get("_response")},
            response=e.response,
        ) from None

    # Attempt pickup scheduling (may fail if courier needs manual pickup time)
    try:
        pickup = schedule_pickup(sr["sr_order_id"])
    except Exception as e:
        log.warning("Pickup scheduling failed (non-fatal): %s", e)
        pickup = {}

    return {
        "sr_order_id": sr["sr_order_id"],
        "shipment_id": sr.get("shipment_id"),
        "awb_code": awb["awb_code"],
        "courier_name": awb["courier_name"],
        "tracking_url": f"https://shiprocket.in/tracking/{awb['awb_code']}",
    }
