"""Live delivery-fee estimates for checkout display.

Quotes come from Borzo's `calculate-order` (the real courier price for the
pin's location) — not the old distance-zone table. The zone table is kept as
the availability + minimum-order source and as an offline fallback.

Free-delivery is currently PAUSED (config.FREE_DELIVERY_ENABLED = False), so
every delivery shows its live fee. Zone fallback respects the same flag so the
two paths never disagree.

Quotes are cached briefly (5 min, bucketed by rounded coords + subtotal) so a
pin drag doesn't hammer the courier API for every pixel move.
"""
import time

from ..config import DELIVERY_ZONES, FREE_DELIVERY_ENABLED

_TTL_S = 300

# Borzo no longer delivers for us (Oct 2026, Porter does), but its
# calculate-order price for the same pin tracks Porter's 2-wheeler fares
# closely, so it stays the live price reference. The zone table is the
# fallback when the call fails.
USE_BORZO_QUOTES = False  # Oct 2026: Borzo's ₹90 floor overstated short Porter trips
_cache: dict[tuple, dict] = {}


def _zone_quote(lat: float, lng: float, subtotal: float, km_hint: float | None = None) -> dict:
    """Zone-table quote used as fallback / availability gate. Min-order and
    serviceability come from here; the fee is a rough estimate only."""
    from ..orders import _haversine_m, delivery_fee
    from .config import PICKUP_LAT, PICKUP_LNG

    km = km_hint if km_hint is not None else _haversine_m(float(lat), float(lng), PICKUP_LAT, PICKUP_LNG) / 1000
    zone = next((z for z in DELIVERY_ZONES if km <= z["max_km"]), None)
    if not zone:
        return {"serviceable": False, "fee": None, "provider": "zones", "km": km}
    if FREE_DELIVERY_ENABLED and subtotal >= FREE_DELIVERY_ABOVE:
        fee = 0.0
    else:
        fee = zone["fee"]
    q = delivery_fee(km, subtotal) or {}
    return {
        "serviceable": True, "fee": fee, "zone": zone["name"], "min_order": zone["min_order"],
        "km": km, "provider": "zones", "note": "estimate",
    }


def _round5(x: float) -> int:
    return int(5 * round(x / 5))


def _with_range(result: dict) -> dict:
    """Delivery is no longer a fixed charge (Oct 2026): the kitchen books a
    Porter rider after the order and the real fare is sent to the customer
    then. Checkout shows a rough range around the best point estimate.

    Point estimate = Borzo's live price for the pin (tracks Porter's
    2-wheeler fares) or, offline, a Porter-like 45 + 10/km formula. The
    range is -15% / +20% of it, never below ₹40."""
    if not result.get("serviceable"):
        return result
    mid = float(result.get("fee") or 0)
    if result.get("provider") != "borzo":
        mid = 45 + 10 * float(result.get("km") or 0)
    result["fee"] = _round5(mid)
    result["fee_low"] = max(40, _round5(mid * 0.85))
    result["fee_high"] = max(result["fee_low"] + 10, _round5(mid * 1.2))
    return result


def estimate(lat: float, lng: float, subtotal: float = 0.0,
             address: str | None = None, km_hint: float | None = None) -> dict:
    """Return the best delivery-fee estimate for a pin.

    Prefers Borzo's live quote; falls back to the zone table on any API
    failure. Never raises — checkout must not break because the courier API
    hiccuped. Includes `min_order`/`serviceable` from the zone gate.
    """
    lat, lng = float(lat), float(lng)
    key = (round(lat, 2), round(lng, 2), subtotal // 100)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit["at"] < _TTL_S:
        return {k: v for k, v in hit.items() if k != "at"}

    result = _zone_quote(lat, lng, subtotal, km_hint)
    if not USE_BORZO_QUOTES:
        # Fixed fee per distance band (config.DELIVERY_ZONES), charged at
        # checkout; the kitchen absorbs the difference from Porter's fare.
        result["note"] = "fixed"
        result["at"] = now
        _cache[key] = result
        return {k: v for k, v in result.items() if k != "at"}
    try:
        from .borzo import calculate_order as borzo_calculate
        q = borzo_calculate(
            delivery_address=address or "Customer delivery address",
            items=[{"qty": 1, "name": "food order"}],
            delivery_lat=str(lat), delivery_lng=str(lng), total=subtotal,
        )
        fee = float(q.get("payment_amount") or q.get("delivery_fee_amount") or 0)
        if fee > 0 and result.get("serviceable"):
            result["fee"] = round(fee)
            result["provider"] = "borzo"
            result["note"] = "live courier rate"
    except Exception:
        pass  # keep the zone fallback

    result = _with_range(result)
    result["at"] = now
    _cache[key] = result
    return {k: v for k, v in result.items() if k != "at"}