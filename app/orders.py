"""Shared order service used by both the web API and the WhatsApp bot."""
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, menu
from .links import extract_tracking_url
from .config import (
    DELIVERY_ZONES,
    FREE_DELIVERY_ABOVE,
    FREE_DELIVERY_ENABLED,
    GST_ENABLED,
    GST_RATE,
    MONDAY_OPENS_AT,
    OPENING_HOURS,
    PACKING_FEE,
    PACKING_FEE_LARGE_ORDER,
    PACKING_FEE_LARGE_ORDER_THRESHOLD,
)

IST = ZoneInfo("Asia/Kolkata")

ADDRESS_EDIT_WINDOW = timedelta(minutes=3)
MAX_SCHEDULE_AHEAD = timedelta(days=14)

_TRACK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_tracking_token() -> str:
    """Short, human-readable token customers reference on WhatsApp/phone.
    No ambiguous chars (I/L/O/0/1). Kept independent of the sequential order
    id so tracking URLs can't be guessed by incrementing a number."""
    import secrets
    return "".join(secrets.choice(_TRACK_ALPHABET) for _ in range(8))

_LANDMARK_RE = re.compile(r"\s*\(Landmark:\s*(.*?)\)\s*$", re.IGNORECASE)


def _normalize_phone(phone: str) -> str:
    """Canonical form: bare 10 digits for Indian numbers, full international
    digits for others. Checkout submits whatever the customer
    typed with no country-code normalization, so `customers.phone` has
    accumulated a mix of "+91XXXXXXXXXX", "91XXXXXXXXXX" and bare
    "XXXXXXXXXX" — which silently broke the /track phone lookup for anyone
    not stored in exactly the format the query assumed. Normalize once here,
    at the door, so storage is consistent going forward."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    # Anything else longer than 10 digits is a foreign number with its
    # country code (e.g. 447557212240): keep it whole, or WhatsApp would
    # get "91" + its last 10 digits — somebody else's Indian number.
    return digits


def _parse_iso(scheduled_at: str) -> datetime:
    s = scheduled_at
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _validate_scheduled_at(scheduled_at: str | None) -> None:
    """scheduled_at arrives as a UTC ISO string (JS Date.toISOString())."""
    if not scheduled_at:
        return
    try:
        when = _parse_iso(scheduled_at).replace(tzinfo=None)  # naive UTC
    except ValueError:
        raise OrderError("Invalid scheduled time", 400)
    now = datetime.utcnow()
    if when < now - timedelta(minutes=5):
        raise OrderError("That time has already passed", 400)
    if when > now + MAX_SCHEDULE_AHEAD:
        raise OrderError("We only take orders up to 2 weeks ahead", 400)


def _validate_scheduled_window(scheduled_window: str | None, scheduled_at: str | None) -> None:
    from .config import DELIVERY_WINDOWS
    if not scheduled_window:
        return
    if scheduled_window not in {w["id"] for w in DELIVERY_WINDOWS}:
        raise OrderError("Unknown delivery window", 400)
    if not scheduled_at:
        raise OrderError("A scheduled window needs a time", 400)


def _window_label(scheduled_window: str | None, scheduled_at: str | None) -> str:
    """Human label for confirm/tracking pages: window name + times + day,
    falling back to the exact time for legacy custom-scheduled orders."""
    from .config import DELIVERY_WINDOWS
    win = next((w for w in DELIVERY_WINDOWS if w["id"] == scheduled_window), None)
    when = _parse_iso(scheduled_at).astimezone(IST) if scheduled_at else None
    day = when.strftime("%a, %d %b") if when else ""
    if win:
        return f"{win['label']} ({win['text']}), {day}".strip(", ")
    if when:
        return when.strftime("%a, %d %b, %I:%M %p")
    return "As soon as possible"


def _split_landmark(address: str) -> tuple[str, str | None]:
    """Split the web checkout's "<address> (Landmark: <text>)" format back
    into its parts, for storing address book entries cleanly."""
    m = _LANDMARK_RE.search(address)
    if not m:
        return address, None
    return _LANDMARK_RE.sub("", address), m.group(1)


class OrderError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def delivery_fee(km: float, subtotal: float = 0.0) -> dict | None:
    """Quote only — no min-order enforcement (for the web UI to display live).
    Kept as fallback estimate when the courier API is unavailable."""
    zone = next((z for z in DELIVERY_ZONES if km <= z["max_km"]), None)
    if not zone:
        return None
    free_waives = FREE_DELIVERY_ENABLED and subtotal >= FREE_DELIVERY_ABOVE
    fee = 0.0 if free_waives else zone["fee"]
    return {"zone": zone["name"], "fee": fee, "min_order": zone["min_order"]}


def delivery_quote(km: float, subtotal: float) -> dict:
    quote = delivery_fee(km, subtotal)
    if quote is None:
        raise OrderError("We don't deliver there yet. You can pick up from the restaurant.", 400)
    if subtotal < quote["min_order"]:
        raise OrderError(
            f"Minimum order for {quote['zone']} is ₹{quote['min_order']}", 400
        )
    return quote


def check_pincode_serviceable(pincode: str) -> dict:
    """Check if a pincode is serviceable via Shiprocket Quick.

    Returns {serviceable: bool, couriers: list, fee_estimate: str}.
    Uses our zone logic for fee estimation since Indian pincodes cover broad areas.
    """
    try:
        from .delivery.shiprocket import check_serviceability
        couriers = check_serviceability(pincode)
        if couriers:
            cheapest = min(couriers, key=lambda c: c.get("freight_charge", 999))
            return {
                "serviceable": True,
                "couriers": couriers,
                "fee_estimate": f"~₹{int(cheapest.get('freight_charge', 0))}",
                "eta": cheapest.get("estimated_delivery_days", ""),
            }
    except Exception:
        pass
    # Fallback: assume serviceable if pincode looks valid (6 digits)
    if pincode and len(pincode) == 6 and pincode.isdigit():
        return {"serviceable": True, "couriers": [], "fee_estimate": "₹30–70", "eta": ""}
    return {"serviceable": False, "couriers": [], "fee_estimate": None, "eta": None}


MAX_NOTE_LEN = 200


def build_lines(items: list[dict]) -> tuple[list[dict], float]:
    """Validate item ids against today's availability; returns (lines, subtotal).

    An optional freeform `note` per item (e.g. "swap dry sabzi for Paneer
    Butter Masala, +2 phulka" on a thali) is appended to the stored item
    name — shows on the kitchen Telegram alert and admin panel as-is. No
    price impact by design (customization pricing is a later decision)."""
    lines, subtotal = [], 0.0
    for it in items:
        m = menu.get_item(it["item_id"])
        if not m:
            raise OrderError(f"Unknown item: {it['item_id']}", 400)
        if not menu.is_available(m["id"]):
            raise OrderError(f"'{m['name']}' is not available today", 409)
        price = float(m["price"])
        subtotal += price * it["qty"]
        name = m["name"]
        note = (it.get("note") or "").strip()[:MAX_NOTE_LEN]
        if note:
            name = f"{name} — {note}"
        lines.append({"item_id": m["id"], "name": name, "price": price, "qty": it["qty"]})
    if not lines:
        raise OrderError("No items in order", 400)
    return lines, subtotal


def packing_fee_for(subtotal: float) -> float:
    if not PACKING_FEE:
        return 0.0
    return PACKING_FEE_LARGE_ORDER if subtotal >= PACKING_FEE_LARGE_ORDER_THRESHOLD else PACKING_FEE


def gst_for(taxable_base: float) -> float:
    """GST on item total + packing charge — delivery fee stays outside the
    taxable base, matching how the aggregator data tracks delivery "without
    tax" separately from the restaurant-service charge."""
    return round(taxable_base * GST_RATE, 2) if GST_ENABLED else 0.0


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def check_address(address: str | None, pincode: str | None,
                  lat: str | None, lng: str | None) -> tuple[bool, str | None]:
    """Best-effort heuristic: does this delivery address need a human look
    before booking a rider? Never raises — a geocoding failure just skips
    the distance check rather than blocking the order.

    The web checkout folds the landmark into `address` as "(Landmark: ...)"
    (see menu.html) — that's the deterministic marker checked for below.
    """
    if not address:
        return False, None
    if "(landmark:" not in address.lower():
        return True, "No landmark given — riders ask for one."
    if not lat or not lng:
        return True, "No GPS pin — only a typed address."
    try:
        from .delivery.shiprocket import geocode_address
        g_lat, g_lng = geocode_address(address, pincode or "")
        if g_lat and g_lng:
            dist = _haversine_m(float(lat), float(lng), float(g_lat), float(g_lng))
            if dist > 300:
                return True, f"Typed address and GPS pin are {int(dist)}m apart."
    except Exception:
        pass
    return False, None


def _hhmm12(hhmm: str) -> str:
    h, m = hhmm.split(":")
    h = int(h)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m} {suffix}".replace(":00 ", " ")


def _open_close_for(weekday: int) -> tuple[str, str]:
    if weekday == 0:  # Monday opens late
        return MONDAY_OPENS_AT, OPENING_HOURS["mon_sat"][1]
    if weekday == 6:
        return OPENING_HOURS["sunday"]
    return OPENING_HOURS["mon_sat"]


def _schedule_closed_reason(scheduled_at: str | None = None,
                            scheduled_window: str | None = None) -> str | None:
    """Fixed weekly hours not covered by the manual store_status toggle.

    Checks against `scheduled_at` (a pre-order's requested time) when given,
    otherwise the current time — so an "as soon as possible" order can't slip
    through outside opening hours. Returns a customer-facing reason, or None
    if within hours."""
    when = _parse_iso(scheduled_at).astimezone(IST) if scheduled_at else datetime.now(IST)
    opens_at, closes_at = _open_close_for(when.weekday())
    opens = datetime.strptime(opens_at, "%H:%M").time()
    closes = datetime.strptime(closes_at, "%H:%M").time()
    if when.time() < opens:
        if when.weekday() == 0 and scheduled_window == "lunch":
            return "We're closed on Monday mornings, so the lunch window isn't available. Please choose dinner."
        return f"We're closed right now. We open again at {_hhmm12(opens_at)}."
    if when.time() >= closes:
        if scheduled_at:
            return "We're closed at that time. Please choose the lunch or dinner window instead."
        return "We're closed right now. You can order ahead for the next lunch or dinner window."
    return None


def create_order(phone: str, name: str, order_type: str, items: list[dict],
                 address: str | None = None, km: float | None = None,
                 pincode: str | None = None,
                 payment_method: str = "cod", instructions: str | None = None,
                 scheduled_at: str | None = None,
                 lat: str | None = None, lng: str | None = None,
                 scheduled_window: str | None = None,
                 # Default true: quote the customer a delivery fee estimate but
                 # don't fold it into what we charge — the rider's actual fee
                 # is set at pickup, not by our zone table, so bundling an
                 # estimate into the total risks over/undercharging. They pay
                 # the rider directly by default; opting into `False` here
                 # means WE collect the (estimated) fee and pay the rider
                 # ourselves instead, for customers who'd rather not deal
                 # with it at the door.
                 pay_courier_direct: bool = False,
                 discount_code: str | None = None) -> dict:
    if order_type not in ("delivery", "pickup"):
        raise OrderError("Invalid order_type", 400)
    phone = _normalize_phone(phone)
    store = db.get_store_status()
    if not store["is_open"]:
        raise OrderError(store["reason"] or "We're closed for online orders right now", 400)
    _validate_scheduled_at(scheduled_at)
    _validate_scheduled_window(scheduled_window, scheduled_at)
    schedule_reason = _schedule_closed_reason(scheduled_at, scheduled_window)
    if schedule_reason:
        raise OrderError(schedule_reason, 400)

    lines, subtotal = build_lines(items)

    delivery_fee = 0.0
    tracking_token = _new_tracking_token()
    if order_type == "delivery":
        if km is not None:
            # Legacy km-based flow (WhatsApp bot fallback)
            quote = delivery_quote(km, subtotal)
            delivery_fee = quote["fee"]
        elif lat and lng:
            # Web checkout: live courier quote for the dropped pin.
            # Falls back to the zone estimate if the courier API is down —
            # never blocks the order for a pricing hiccup.
            try:
                from .delivery.estimate import estimate
                q = estimate(float(lat), float(lng), subtotal, address)
                if q.get("serviceable") is False:
                    raise OrderError("We don't deliver there yet. You can pick up from the restaurant.", 400)
                delivery_fee = q["fee"] if q.get("fee") is not None else None
            except OrderError:
                raise
            except Exception:
                delivery_fee = None
            if delivery_fee is None:
                delivery_fee = delivery_fee_from_pincode(pincode or "600018", subtotal)
        elif pincode:
            # Pincode-based fallback: zone estimate, actual fee set at dispatch
            delivery_fee = delivery_fee_from_pincode(pincode, subtotal)
        else:
            raise OrderError("Delivery distance or pincode is required", 400)

    # Discount codes come off the food subtotal, before packing and GST.
    discount, applied_code = 0.0, None
    if discount_code and discount_code.strip():
        from .discounts import DiscountError, evaluate
        try:
            d = evaluate(discount_code, phone, subtotal)
        except DiscountError as e:
            raise OrderError(str(e), 400)
        discount, applied_code = d["amount"], d["code"]

    packing_fee = packing_fee_for(subtotal)
    gst_amount = gst_for(subtotal - discount + packing_fee)
    # Delivery is a fixed fee by distance band, charged in the total. The
    # actual Porter fare is stored as courier_cost when the owner reports it.
    total = subtotal - discount + packing_fee + gst_amount + (delivery_fee or 0.0)
    flagged, flag_reason = check_address(address, pincode, lat, lng)
    cid = db.upsert_customer(phone, name, address, pincode)
    oid = db.create_order(cid, order_type, subtotal, delivery_fee, total,
                          payment_method, instructions, lines,
                          delivery_address=address, delivery_pincode=pincode,
                          delivery_lat=lat, delivery_lng=lng,
                          scheduled_at=scheduled_at,
                          address_flagged=flagged, address_flag_reason=flag_reason,
                          packing_fee=packing_fee, gst_amount=gst_amount,
                          tracking_token=tracking_token,
                          scheduled_window=scheduled_window,
                          pay_courier_direct=pay_courier_direct,
                          discount_total=discount, discount_code=applied_code)
    if order_type == "delivery" and address:
        base_address, landmark = _split_landmark(address)
        try:
            db.add_customer_address(cid, base_address, landmark=landmark, lat=lat, lng=lng)
        except Exception:
            pass  # address book is a convenience, never block order creation

    # Customer confirmation on WhatsApp (template), never blocks the order.
    try:
        from .notify import notify_order_placed
        placed = db.get_order(oid)
        if placed is not None:
            notify_order_placed(placed)
    except Exception:
        pass

    # Kitchen alert (the owner) — Telegram DMs her, never blocks the order flow.
    try:
        from .telegram import notify_new_order
        order_row = db.get_order(oid)
        if order_row is not None:
            notify_new_order(order_row)
    except Exception:
        pass

    # Push to Petpooja POS — no-op until PETPOOJA_APP_KEY etc. are set (see
    # app/petpooja/config.py); never blocks order creation on failure. The
    # relay outcome is surfaced back in the API response so a silent
    # success=1-with-blank-orderID is visible to whoever placed the order
    # (dashboard/Cli), not just in server logs.
    petpooja_info: dict = {}
    try:
        from .petpooja.client import is_configured, save_order as petpooja_save_order
        order_row = db.get_order(oid) if is_configured() else None
        if order_row and any(menu.is_custom(i["item_id"] or "") for i in order_row["items"]):
            # A dish added by hand isn't on the POS, and the POS must never be
            # sent a line it doesn't know. The kitchen screen flags the order.
            petpooja_info = {"petpooja_skipped": "order has a dish that is not on the POS"}
        elif order_row:
            from .config import SITE_URL
            from .petpooja.config import PETPOOJA_WEBHOOK_TOKEN
            callback_url = f"{SITE_URL}/webhook/petpooja/order-callback?t={PETPOOJA_WEBHOOK_TOKEN}"
            result = petpooja_save_order(order_row, callback_url, GST_RATE)
            db.update_order_petpooja(oid, result["petpooja_order_id"])
            petpooja_info = {
                "petpooja_order_id": result["petpooja_order_id"],
                "client_order_id": result["client_order_id"],
                "petpooja_message": result.get("message", ""),
                "petpooja_rest_id": result.get("rest_id", ""),
                "petpooja_raw": result.get("raw", {}),
            }
    except Exception as e:
        petpooja_info = {"petpooja_error": str(e)[:300]}
        import logging
        logging.getLogger("petpooja").exception("save_order push failed for order %s", oid)

    return {"order_id": oid, "status": "new", "subtotal": round(subtotal, 2),
            "packing_fee": packing_fee, "gst_amount": gst_amount,
            "delivery_fee": delivery_fee, "total": round(total, 2),
            "discount": discount, "discount_code": applied_code,
            "tracking_token": tracking_token,
            "scheduled_window": scheduled_window, "pay_courier_direct": pay_courier_direct,
            "schedule_label": _window_label(scheduled_window, scheduled_at),
            **petpooja_info}


def edit_address(order_id: int, address: str, pincode: str | None = None,
                 lat: str | None = None, lng: str | None = None) -> dict:
    """Let a customer fix their delivery address within a short window after
    ordering — before the kitchen has started and before a rider is booked."""
    o = db.get_order(order_id)
    if not o:
        raise OrderError("Order not found", 404)
    if o["order_type"] != "delivery":
        raise OrderError("Only delivery orders have an address to change", 400)
    if o["status"] != "new":
        raise OrderError("Too late to change this. The kitchen has already started on your order.", 400)
    created = datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M:%S")
    if datetime.utcnow() - created > ADDRESS_EDIT_WINDOW:
        raise OrderError("The 3-minute window to change the address has passed", 400)

    flagged, flag_reason = check_address(address, pincode or o.get("delivery_pincode"), lat, lng)
    db.update_order_address(order_id, address, lat, lng, address_flagged=flagged,
                            address_flag_reason=flag_reason)
    return {"ok": True, "order_id": order_id, "address": address}


def delivery_fee_from_pincode(pincode: str, subtotal: float) -> float:
    """Estimate delivery fee from pincode using zone logic.

    Since Indian pincodes cover broad areas, we use our zone estimates.
    The actual fee is calculated by the courier at dispatch time.
    """
    if FREE_DELIVERY_ENABLED and subtotal >= FREE_DELIVERY_ABOVE:
        return 0.0
    # Default to Zone A fee as estimate; dispatch corrects if needed
    return DELIVERY_ZONES[0]["fee"]


def dispatch_rider(order_id: int) -> dict:
    """Book a rider for a delivery order — Porter only (Oct 2026).

    With PORTER_API_KEY set this books through Porter's API and marks the
    order out_for_delivery. Until then it returns {"manual": True, ...} with
    the booking card: the kitchen books in the Porter app and records the
    rider via record_manual_dispatch(). Borzo/Shiprocket are no longer used
    (unreliable in our radius); their modules stay in app/delivery/ unused.

    Called by the admin Book button and when Petpooja's callback reports the
    order food-ready (app/webhooks.py `_maybe_auto_dispatch`).
    """
    o = _dispatchable_order(order_id)
    from .delivery import porter

    if not porter.api_configured():
        return {"provider": "porter", "manual": True,
                "booking": porter.booking_details(o),
                "booking_text": porter.booking_text(o)}

    result = porter.book(o)
    db.update_order_dispatch(
        order_id=order_id, sr_order_id=None,
        awb=str(result.get("porter_order_id", "")),
        courier=result.get("courier_name") or "Porter",
        tracking_url=result.get("tracking_url", ""),
    )
    return {"provider": "porter", "manual": False, **result}


def _dispatchable_order(order_id: int) -> dict:
    o = db.get_order(order_id)
    if not o:
        raise OrderError("Order not found", 404)
    if o["order_type"] != "delivery":
        raise OrderError("Cannot dispatch pickup orders", 400)
    if o["status"] not in ("preparing", "ready"):
        raise OrderError(f"Order must be preparing or ready to book a rider (currently {o['status']})", 400)
    if not o.get("delivery_address"):
        raise OrderError("Order missing delivery address", 400)
    if o.get("sr_tracking_url") or o.get("sr_courier"):
        raise OrderError("Rider already booked for this order", 400)
    return o


def record_delivery_fee(order_id: int, fee: float) -> dict:
    """Store the real Porter fare for a booked delivery order.

    Fixed-fee orders (the customer already paid delivery in the total) keep
    it as courier_cost only. Older pay-after-booking orders (estimate range,
    delivery_fee 0) get it as the fare the customer still owes."""
    o = db.get_order(order_id)
    if not o:
        raise OrderError("Order not found", 404)
    if o["order_type"] != "delivery":
        raise OrderError("Not a delivery order", 400)
    fee = round(float(fee), 2)
    if not 10 <= fee <= 1000:
        raise OrderError(f"₹{fee:g} doesn't look like a delivery fare", 400)
    if is_fee_later(o):
        db.set_delivery_fee_final(order_id, fee)
    else:
        db.set_courier_cost(order_id, fee)
    return db.get_order(order_id)


def is_fee_later(order: dict) -> bool:
    """Order placed under the old pay-after-booking scheme (Oct 7-13 2026)."""
    if order.get("delivery_fee_final"):
        return True
    return bool(order.get("delivery_fee_low")) and not float(order.get("delivery_fee") or 0)


def record_manual_dispatch(order_id: int, tracking_url: str, rider_name: str = "",
                           rider_phone: str = "", fare: float | None = None) -> dict:
    """Kitchen booked the rider in the Porter app: store the tracking link and
    rider, mark out_for_delivery. Returns the dispatch dict notify_dispatch wants."""
    _dispatchable_order(order_id)
    tracking_url = (tracking_url or "").strip()
    if tracking_url:
        found = extract_tracking_url(tracking_url)
        if found["state"] in ("none", "store"):
            raise OrderError("That doesn't have a rider link in it. Copy the link from Share and paste again.", 400)
        tracking_url = found["url"]
    courier = "Porter" + (f" · {rider_name.strip()}" if rider_name.strip() else "")
    db.update_order_dispatch(order_id=order_id, sr_order_id=None,
                             awb=rider_phone.strip(), courier=courier,
                             tracking_url=tracking_url)
    if fare:
        record_delivery_fee(order_id, fare)
    return {"provider": "porter", "manual": True, "courier_name": courier,
            "tracking_url": tracking_url, "rider_phone": rider_phone.strip(),
            "delivery_fee": float(fare) if fare else None}
