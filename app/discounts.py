"""Discount codes, kept in the database so marketing can add, pause or
expire them without a deploy (admin API: /api/admin/discount-codes).

A code is checked twice: when the customer taps Apply (preview) and again
inside orders.create_order, which is the only place a discount is granted.
The discount comes off the food subtotal, before packing, GST and delivery.
"""
from datetime import datetime, timedelta, timezone

from . import db

IST = timezone(timedelta(hours=5, minutes=30))

# Created on first start so there is something to hand out; edit or pause it
# through the admin API like any other code.
SEED_CODES = [
    {"code": "TRIAL15", "kind": "percent", "value": 15, "max_discount": 100,
     "once_per_customer": 1, "first_order_only": 1,
     "note": "15% off your first order, up to ₹100"},
]


class DiscountError(ValueError):
    pass


def _norm(code: str | None) -> str:
    return "".join((code or "").split()).upper()


def seed() -> None:
    conn = db.get_conn()
    for c in SEED_CODES:
        conn.execute(
            "INSERT OR IGNORE INTO discount_codes(code, kind, value, max_discount, "
            "once_per_customer, first_order_only, note) VALUES(?,?,?,?,?,?,?)",
            (c["code"], c["kind"], c["value"], c.get("max_discount"),
             c.get("once_per_customer", 1), c.get("first_order_only", 0), c.get("note")))
    # TRIAL15 was first seeded as "once per customer". The launch material promises
    # "first order", so tighten it, but only if nobody has edited the row since.
    conn.execute(
        "UPDATE discount_codes SET first_order_only=1, note='15% off your first order, up to ₹100' "
        "WHERE code='TRIAL15' AND first_order_only=0 AND note='15% off, once per customer, up to ₹100'")
    conn.commit()
    conn.close()


def public_offer() -> dict | None:
    """The one active, unexpired code worth advertising on the site (newest first),
    e.g. {'code': 'TRIAL15', 'headline': '15% off your first order', 'detail': 'up to ₹100'}.
    None when no code is live, so pages simply show nothing."""
    now = datetime.now(IST).isoformat()
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM discount_codes WHERE active=1 ORDER BY created_at DESC").fetchall()
    conn.close()
    for r in rows:
        r = dict(r)
        if r.get("expires_at") and str(r["expires_at"]) < now[:len(str(r["expires_at"]))]:
            continue
        if r.get("max_uses") and _uses(r["code"]) >= r["max_uses"]:
            continue
        v = int(r["value"]) if float(r["value"]).is_integer() else r["value"]
        head = f"{v}% off" if r["kind"] == "percent" else f"₹{v} off"
        if r.get("once_per_customer"):
            head += ", once per customer"
        detail = f"up to ₹{int(r['max_discount'])}" if r.get("max_discount") else ""
        return {"code": r["code"], "headline": head, "detail": detail,
                "text": f"Use code {r['code']} for {head}" + (f" ({detail})" if detail else "")}
    return None


def get(code: str) -> dict | None:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM discount_codes WHERE code=?", (_norm(code),)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_codes() -> list[dict]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM orders o WHERE o.discount_code=d.code "
        "AND o.status != 'cancelled') AS used FROM discount_codes d ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert(data: dict) -> dict:
    code = _norm(data.get("code"))
    if not code or not code.isalnum() or len(code) > 20:
        raise DiscountError("Codes are 1 to 20 letters or digits, no spaces.")
    kind = data.get("kind", "percent")
    if kind not in ("percent", "flat"):
        raise DiscountError("kind must be 'percent' or 'flat'.")
    value = float(data.get("value") or 0)
    if value <= 0 or (kind == "percent" and value > 100):
        raise DiscountError("value must be above 0 (and at most 100 for percent).")
    fields = {
        "kind": kind, "value": value,
        "max_discount": data.get("max_discount"),
        "min_subtotal": float(data.get("min_subtotal") or 0),
        "once_per_customer": 1 if data.get("once_per_customer", True) else 0,
        "first_order_only": 1 if data.get("first_order_only") else 0,
        "max_uses": data.get("max_uses"),
        "active": 1 if data.get("active", True) else 0,
        "expires_at": data.get("expires_at") or None,
        "note": data.get("note"),
    }
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO discount_codes(code, " + ", ".join(fields) + ") VALUES(?, "
        + ", ".join("?" for _ in fields) + ") ON CONFLICT(code) DO UPDATE SET "
        + ", ".join(f"{k}=excluded.{k}" for k in fields),
        (code, *fields.values()))
    conn.commit()
    conn.close()
    return get(code)


def _uses(code: str, phone: str | None = None) -> int:
    conn = db.get_conn()
    sql = ("SELECT COUNT(*) FROM orders o JOIN customers c ON c.id=o.customer_id "
           "WHERE o.discount_code=? AND o.status != 'cancelled'")
    args: tuple = (code,)
    if phone:
        sql += " AND c.phone=?"
        args += (phone,)
    n = conn.execute(sql, args).fetchone()[0]
    conn.close()
    return n


def _past_orders(phone: str) -> int:
    conn = db.get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM orders o JOIN customers c ON c.id=o.customer_id "
        "WHERE c.phone=? AND o.status != 'cancelled'", (phone,)).fetchone()[0]
    conn.close()
    return n


def evaluate(code: str | None, phone: str | None, subtotal: float) -> dict:
    """{'code', 'amount', 'label'} for a usable code, else DiscountError with
    a message to show the customer. `phone` is the normalised order phone."""
    code = _norm(code)
    d = get(code) if code else None
    if not d or not d["active"]:
        raise DiscountError(f"{code or 'That code'} isn't a valid code.")
    if d["expires_at"]:
        try:
            exp = datetime.fromisoformat(d["expires_at"])
        except ValueError:
            exp = None  # unreadable date: treat as no expiry rather than block everyone
        if exp:
            exp = exp if exp.tzinfo else exp.replace(tzinfo=IST)
            if len(d["expires_at"]) == 10:  # a date: valid through that day
                exp += timedelta(days=1)
            if datetime.now(IST) >= exp:
                raise DiscountError(f"{code} has expired.")
    if subtotal < (d["min_subtotal"] or 0):
        raise DiscountError(f"{code} needs a food total of ₹{d['min_subtotal']:g} or more.")
    if d["max_uses"] is not None and _uses(code) >= d["max_uses"]:
        raise DiscountError(f"{code} has been fully used up.")
    if phone:
        if d["once_per_customer"] and _uses(code, phone):
            raise DiscountError(f"You've already used {code} on an earlier order.")
        if d["first_order_only"] and _past_orders(phone):
            raise DiscountError(f"{code} is for first orders only.")
    if d["kind"] == "percent":
        amount = subtotal * d["value"] / 100
        if d["max_discount"]:
            amount = min(amount, float(d["max_discount"]))
        label = f"{d['value']:g}% off" + (f" (up to ₹{d['max_discount']:g})" if d["max_discount"] else "")
    else:
        amount = float(d["value"])
        label = f"₹{d['value']:g} off"
    amount = round(min(amount, subtotal), 2)
    return {"code": code, "amount": amount, "label": label}
