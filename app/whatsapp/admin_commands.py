"""WhatsApp commands for the owner, typed from the shop phone to the bot number.

  "orders"                 → today's orders and what each is waiting for
  "paid 42"                → UPI received for order 42 (customer is told)
  "dal sold out"           → mark item unavailable today
  "chola bhatura back"     → mark item available again
  "special mango lassi 89" → set today's special
  "all on"                 → everything available
  anything else            → this list, in plain words
Swipe-replies to alerts (Porter card, customer questions) are handled in
kitchen_alerts.handle_admin_reply before these.
"""
import re
from datetime import date

from .. import db, menu


HELP = (
    "Hi! Here's what you can send me:\n\n"
    "📋 *orders* — today's orders\n"
    "💰 *paid 42* — UPI received for order 42\n"
    "🚫 *dal tadka sold out* — hide a dish for today\n"
    "✅ *dal tadka back* — show it again\n"
    "⭐ *special mango lassi 89* — today's special\n\n"
    "To answer a customer or send a Porter link, swipe right on my message and type."
)

STATUS_WORD = {"new": "new, accept on POS", "preparing": "cooking", "ready": "packed",
               "out_for_delivery": "on the way", "delivered": "delivered", "cancelled": "cancelled"}


def is_admin_command(text: str) -> bool:
    t = text.strip().lower()
    return any([
        t in ("help", "hi", "hello", "menu", "?", "orders", "today", "list"),
        re.fullmatch(r"paid\s*#?\d+", t) is not None,
        "sold out" in t,
        t.endswith(" back"),
        t.startswith("special "),
        t in ("all on", "all available", "everything on"),
        t in ("spend", "bill", "whatsapp bill"),
    ])


def _spend() -> str:
    from . import client
    try:
        s = client.spend(30)
    except Exception:
        return "Couldn't fetch the WhatsApp bill right now. Try again later."
    paid = [r for r in s["rows"] if r["cost"]]
    free = sum(r["volume"] for r in s["rows"] if not r["cost"])
    lines = [f"💸 WhatsApp, last 30 days: ₹{s['total']:.2f}"]
    lines += [f"• {r['volume']} {r['category'].lower()} to {r['country']}: ₹{r['cost']:.2f}" for r in paid[:6]]
    lines.append(f"• {free} free messages")
    return "\n".join(lines)


def handle_admin_command(text: str) -> str:
    t = text.strip().lower()
    today = date.today().isoformat()

    if t in ("orders", "today", "list"):
        return _orders_today()
    if t in ("spend", "bill", "whatsapp bill"):
        return _spend()

    m_paid = re.fullmatch(r"paid\s*#?(\d+)", t)
    if m_paid:
        return _mark_paid(int(m_paid.group(1)))

    # "all on" / "all available"
    if t in ("all on", "all available", "everything on"):
        all_ids = [m["id"] for m in menu.load_menu()]
        db.set_availability(today, all_ids, [])
        return f"All {len(all_ids)} items set to available."

    # "special mango lassi 89"
    m_special = re.match(r"^special\s+(.+?)\s*(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(?:rs\.?|rupees|/-|₹)?$", t)
    if m_special:
        item_name = m_special.group(1).strip().title()
        price = float(m_special.group(2))
        db.set_special(today, item_name, price)
        db.save_custom_item(menu.custom_item_id(item_name), item_name, price, "", today)
        return f"Today's special is on the menu: {item_name} — ₹{int(price)}"

    # "dal sold out"
    if "sold out" in t:
        item_name = t.replace("sold out", "").strip()
        if not item_name:
            return "Usage: <item name> sold out"
        matched = _find_menu_item(item_name)
        if not matched:
            return f"Couldn't find '{item_name}' in the menu."
        avail = db.get_available_ids(today)
        db.set_availability(today, [], [matched["id"]])
        return f"Marked {matched['name']} as sold out."

    # "chola bhatura back"
    if t.endswith(" back"):
        item_name = t[:-4].strip()
        if not item_name:
            return "Usage: <item name> back"
        matched = _find_menu_item(item_name)
        if not matched:
            return f"Couldn't find '{item_name}' in the menu."
        db.set_availability(today, [matched["id"]], [])
        return f"Marked {matched['name']} as available."

    return HELP


def _find_menu_item(query: str) -> dict | None:
    """Fuzzy match a menu item name. Tries exact, then substring, then word match."""
    q = query.lower().strip()
    items = menu.load_menu()

    # Exact match (case-insensitive)
    for it in items:
        if it["name"].lower() == q:
            return it

    # Substring match
    for it in items:
        if q in it["name"].lower():
            return it

    # Word match — all words in query appear in item name
    words = q.split()
    for it in items:
        name_lower = it["name"].lower()
        if all(w in name_lower for w in words):
            return it

    return None


def _orders_today() -> str:
    rows = [o for o in db.today_orders() if o.get("status") != "cancelled"]
    if not rows:
        return "No website or WhatsApp orders today yet."
    lines = [f"📋 Today's orders ({len(rows)}):"]
    for o in rows[-15:]:
        pay = "UPI ✅" if o.get("paid") else ("UPI ⏳ not confirmed" if o.get("payment_method") == "upi" else "Cash")
        kind = "Pickup" if o.get("order_type") == "pickup" else "Delivery"
        lines.append(f"#{o['id']} · {o.get('customer_name') or 'Customer'} · ₹{round(float(o.get('total') or 0))} · "
                     f"{kind} · {pay} · {STATUS_WORD.get(o.get('status'), o.get('status'))}")
    return "\n".join(lines)


def _mark_paid(order_id: int) -> str:
    o = db.get_order(order_id)
    if not o:
        return f"I can't find order #{order_id}. Send *orders* to see today's list."
    if o.get("paid"):
        return f"Order #{order_id} is already marked paid."
    db.mark_order_paid(order_id)
    try:
        from ..notify import notify_payment_received
        notify_payment_received(db.get_order(order_id))
    except Exception:
        pass
    return f"✅ Order #{order_id} marked paid (₹{round(float(o.get('total') or 0))}). The customer has been told."
