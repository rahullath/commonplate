"""Telegram admin-alert notifier — tells the owner about new orders without Meta.

Unlike WhatsApp/SMS (customer channels), this is an internal kitchen alert that
works immediately: no Meta verification, no sender-ID registration, no cost.
the owner installs Telegram (free), taps her bot's /link, and every new order DMs her.

Disabled when TELEGRAM_BOT_TOKEN is empty (default). The admin page beep remains
the fallback so the kitchen still notices without Telegram.
"""
import logging

import httpx

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SITE_URL
from .order_actions import sign

log = logging.getLogger("telegram")

API = "https://api.telegram.org/bot{token}"


def enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the owner's chat. Returns True if Telegram accepted it."""
    if not enabled():
        return False
    if len(text) > 4000:  # Telegram hard limit
        text = text[:3996] + "…"
    try:
        r = httpx.post(
            f"{API.format(token=TELEGRAM_BOT_TOKEN)}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode,
                # Telegram fetches every link in a message server-side to build a
                # preview — with action links below that are GET requests that
                # cancel an order or mark it paid, an un-disabled preview fetch
                # would fire the action itself before the owner ever opens the chat.
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        data = r.json()
        ok = data.get("ok", False)
        if not ok:
            log.error("Telegram send failed: %s", data.get("description"))
        return ok
    except Exception:
        log.exception("Telegram send exception")
        return False


def notify_new_order(order: dict) -> bool:
    """Alert the owner that a new order just came in."""
    oid = order["id"]
    customer = order.get("customer_name") or "Customer"
    total = order.get("total", 0)
    n_items = len(order.get("items") or [])
    lines = "\n".join(f"• {i['name']} × {i['qty']}" for i in (order.get("items") or [])[:8])
    extra = "" if n_items <= 8 else f"\n… and {n_items - 8} more"
    order_type = "Pickup" if order.get("order_type") == "pickup" else "Delivery"
    pay = (order.get("payment_method") or "cod").upper()
    when = (order.get("scheduled_window") or "").capitalize()
    if when:
        text = (
            f"🛎 <b>New Order #{oid}</b> — {order_type} · {when} window\n"
            f"👤 {customer}\n"
            f"💳 {pay} · ₹{total}\n"
            f"{lines}{extra}\n"
        )
    else:
        text = (
            f"🛎 <b>New Order #{oid}</b> — {order_type}\n"
            f"👤 {customer}\n"
            f"💳 {pay} · ₹{total}\n"
            f"{lines}{extra}\n"
        )
    if order.get("pay_courier_direct"):
        text += f"🛵 Rider collects the delivery fee (₹{order.get('delivery_fee') or 0}) directly\n"
    if order_type == "Delivery" and order.get("delivery_address"):
        text += f"📍 {order['delivery_address']}\n"
    if order.get("delivery_pincode"):
        text += f"📮 {order['delivery_pincode']}\n"
    text += f"<a href='{SITE_URL}/?order={oid}'>Open order in admin →</a>\n"
    text += (
        f"<a href='{SITE_URL}/order-actions/{oid}/paid?sig={sign(oid, 'paid')}'>"
        f"💰 Mark Paid</a>  ·  "
        f"<a href='{SITE_URL}/order-actions/{oid}/cancel?sig={sign(oid, 'cancel')}'>"
        f"❌ Cancel Order</a>"
    )
    return send_message(text)


def notify_status(order: dict, status: str) -> bool:
    """Send the owner a short status ping (dispatch/delivery completion)."""
    oid = order["id"]
    labels = {
        "preparing": "started cooking",
        "ready": "is ready to dispatch",
        "out_for_delivery": "rider on the way",
        "delivered": "marked delivered ✓",
        "cancelled": "was cancelled",
    }
    text = f"🛎 Order #{oid} {labels.get(status, status)}."
    return send_message(text)
