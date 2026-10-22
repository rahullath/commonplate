"""Kitchen alerts: messages to the owner's phone, driven by the POS.

Channel: WhatsApp from the business number to ADMIN_PHONE (the owner's personal
number, wa_id form e.g. "9198XXXXXXXX"), with Telegram as a second channel
when configured. Meta only allows free-form text inside 24 h of the owner's last
message to the business number; outside that window it must be an approved
template: kitchen_alert (app/whatsapp/templates.py, created on Meta
automatically; KITCHEN_WA_TEMPLATE overrides the name).

The Porter loop this powers:
  POS Accept      -> Porter booking card to the owner
  POS Food Ready  -> reminder if no rider recorded yet
  the owner forwards the Porter tracking link to the business WhatsApp
                  -> handle_admin_tracking_link() records it, notifies customer
"""
from .business import business
import logging
import os
import re

from .config import ADMIN_PHONE, WHATSAPP_ACTIVE
from .links import extract_tracking_url

log = logging.getLogger("kitchen")

KITCHEN_WA_TEMPLATE_LANG = os.environ.get("KITCHEN_WA_TEMPLATE_LANG", "en")


def _template_safe(text: str) -> str:
    # Template variables may not contain newlines, tabs or 4+ spaces in a row.
    return re.sub(r"\s{2,}", " ", text.replace("\n", " | ")).strip()[:900]


def send(text: str, html_text: str | None = None, relay: dict | None = None,
         template: tuple | None = None, fallback_template: tuple | None = None) -> list[str]:
    """Send `text` to the kitchen on every configured channel; returns the
    channels that accepted it. Never raises. `relay` ({"kind", "customer",
    "order_id"}) records what the WhatsApp alert was about, so when the owner
    swipe-replies to it the reply reaches the right customer / order."""
    sent = []
    if WHATSAPP_ACTIVE and ADMIN_PHONE:
        from .whatsapp import client
        from .whatsapp.templates import KITCHEN_ALERT
        result = None
        # Cheapest first: the specific UTILITY templates (₹0.115, free while
        # the owner's 24 h window is open), then free text (free, but only reaches
        # her if she wrote to the bot in the last 24 h), and only then the
        # generic alert, which Meta bills as MARKETING (~₹0.8).
        # A template tuple is (name, values) or (name, values, button_suffix).
        from .whatsapp.templates import NOT_UTILITY

        def _try(name, values, button=None):
            try:
                return client.send_template(ADMIN_PHONE, name, KITCHEN_WA_TEMPLATE_LANG,
                                            [{"type": "text", "text": v} for v in values],
                                            button_url_suffix=button)
            except Exception as e:
                log.warning("kitchen template %s failed (%s)", name, e)
                return None
        for t in (template, fallback_template):
            if t and result is None and t[0] not in NOT_UTILITY:
                result = _try(t[0], [_template_safe(v) for v in t[1]], t[2] if len(t) > 2 else None)
        if result is None:
            try:
                result = client.send_text(ADMIN_PHONE, text)
            except Exception as e:
                log.warning("kitchen free-text alert failed (%s)", e)
        if result is None:
            result = _try(KITCHEN_ALERT, [_template_safe(text)])
        if result is None:
            log.error("kitchen WhatsApp alert failed on every route")
        if result is not None:
            sent.append("whatsapp")
            if relay:
                _remember(client.message_id(result), relay)
    from . import telegram
    if telegram.enabled():
        from html import escape
        if telegram.send_message(html_text or escape(text)):
            sent.append("telegram")
    if not sent:
        log.warning("kitchen alert not delivered on any channel: %s", text[:120])
    return sent


def _remember(wamid: str | None, relay: dict) -> None:
    try:
        from . import db
        db.add_relay(wamid, relay.get("kind", "order"), relay.get("customer"), relay.get("order_id"))
    except Exception:
        log.exception("could not store relay mapping")


def _active_order(wa_id: str) -> dict | None:
    """The customer's most recent order today that isn't finished."""
    from . import db
    digits = "".join(ch for ch in wa_id if ch.isdigit())
    for o in db.today_orders():
        phone = "".join(ch for ch in (o.get("customer_phone") or "") if ch.isdigit())
        if phone and digits.endswith(phone[-10:]) and o.get("status") not in ("delivered", "cancelled"):
            return o
    return None


def _who(wa_id: str, name: str | None) -> str:
    d = "".join(ch for ch in wa_id if ch.isdigit())
    shown = f"+{d}" if len(d) > 10 else f"+91{d}"
    return f"{name or 'A customer'} ({shown})"


def relay_question(wa_id: str, name: str | None, text: str) -> bool:
    """A customer's free-text message, forwarded to the owner. She answers by
    swiping right on it and typing; handle_admin_reply() sends it back."""
    o = _active_order(wa_id)
    about = f" about order #{o['id']} ({o.get('status', '').replace('_', ' ')})" if o else ""
    msg = f"💬 {_who(wa_id, name)} asks{about}: \"{text.strip()[:600]}\""
    from .whatsapp.templates import KITCHEN_MESSAGE
    who = _who(wa_id, name) + (f", order #{o['id']}" if o else "")
    return bool(send(msg, relay={"kind": "question", "customer": wa_id,
                                 "order_id": o["id"] if o else None},
                     template=(KITCHEN_MESSAGE, [who, text.strip()[:600]])))


def forward_media(wa_id: str, name: str | None, media_type: str, media_id: str,
                  caption: str | None = None) -> bool:
    """A photo / voice note / document from a customer, re-sent to the owner (Meta
    media ids can't be re-sent as-is, so it's downloaded and re-uploaded).
    Falls back to a text alert if the media can't be sent."""
    from .whatsapp import client
    o = _active_order(wa_id)
    what = {"image": "a photo", "audio": "a voice note", "video": "a video",
            "document": "a document"}.get(media_type, "a file")
    hint = (" Looks like a payment screenshot: check your UPI app, then reply 'paid "
            f"{o['id']}'." if o and media_type == "image" and (o.get("payment_method") == "upi")
            and not o.get("paid") else "")
    label = f"📎 {_who(wa_id, name)} sent {what}" + (f" about order #{o['id']}" if o else "") + "." + hint
    relay = {"kind": "question", "customer": wa_id, "order_id": o["id"] if o else None}
    if WHATSAPP_ACTIVE and ADMIN_PHONE:
        try:
            new_id = client.reupload_media(media_id)
            result = client.send_media(ADMIN_PHONE, media_type, new_id,
                                       caption=(label + (f"\n\n“{caption}”" if caption else ""))[:1000])
            _remember(client.message_id(result), relay)
            return True
        except Exception as e:
            log.warning("media forward failed (%s); sending a text alert", e)
    from .whatsapp.templates import KITCHEN_MESSAGE
    who = _who(wa_id, name) + (f", order #{o['id']}" if o else "")
    note = f"[Sent {what}{' with: ' + caption if caption else ''}]" + hint
    return bool(send(label + " Open WhatsApp on the shop phone to see it." if not hint else label,
                     relay=relay, template=(KITCHEN_MESSAGE, [who, note])))


def handle_admin_reply(text: str, context_wamid: str | None) -> str | None:
    """the owner swiped right on an alert and typed. Returns the reply for the owner, or
    None if the quoted message isn't one we track (caller handles it)."""
    from . import db
    rel = db.get_relay(context_wamid)
    if not rel:
        return None
    if rel["kind"] == "order" and rel.get("order_id"):
        # Reply to a Porter card: the link / fare is for that order.
        if looks_like_tracking_link(text):
            return handle_admin_tracking_link(f"#{rel['order_id']} {text}")
        return (f"For order #{rel['order_id']}, send the Porter link and the fare, "
                f"e.g. \"https://porter.in/… ₹86\".")
    if rel["kind"] == "question" and rel.get("customer"):
        from .whatsapp import client
        try:
            client.send_text(rel["customer"], f"👩‍🍳 From the {business.name} kitchen:\n{text.strip()[:3000]}")
            return "✅ Sent to the customer."
        except Exception as e:
            log.warning("relay to customer failed: %s", e)
            return ("❌ Couldn't send: the customer's chat has been quiet for over 24 hours, "
                    "so WhatsApp won't let us message them. Please call them instead.")
    return None


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,") + "…"


def porter_card(order: dict, status_line: str) -> list[str]:
    """Ask the owner to book Porter for a delivery order: a short card with the drop,
    the customer and what the rider collects, plus an Open order button to the
    kitchen app (which has every detail with Copy buttons)."""
    from .whatsapp.templates import KITCHEN_RIDER, KITCHEN_RIDER_V2
    from html import escape
    from .delivery import porter
    oid = str(order["id"])
    addr = order.get("delivery_address") or ""
    landmark = ""
    if "(Landmark:" in addr:
        addr, _, landmark = addr.partition("(Landmark:")
        landmark = landmark.rstrip(") ").strip()
    if landmark and not re.match(r"(?i)(near|next to|opp|opposite|behind|beside|above|below|in front)\b", landmark):
        landmark = "near " + landmark
    drop = _short(addr.strip().rstrip(","), 110) + (f" ({_short(landmark[:1].lower() + landmark[1:], 45)})" if landmark else "")
    phone = "".join(c for c in (order.get("customer_phone") or "") if c.isdigit())
    phone = f"{phone[:5]} {phone[5:]}" if len(phone) == 10 else ("+" + phone if phone else "")
    who = f"{order.get('customer_name') or 'Customer'} · {phone}".strip(" ·")
    total = float(order.get("total") or 0)
    if (order.get("payment_method") or "").lower() == "cod":
        money = f"Cash: rider collects ₹{total:.0f}."
    elif order.get("paid"):
        money = "Paid by UPI. Rider collects nothing."
    else:
        money = f"UPI ₹{total:.0f} not confirmed yet. Check before the rider leaves."
    if order.get("address_flagged"):
        status_line += ". ⚠ Pin and address don't match: call the customer first"
    pin = porter.booking_details(order).get("drop_map") or ""
    kitchen_link = f"{business.site_url}/kitchen/o/{oid}"
    text = (f"🛵 Book a Porter rider\nOrder #{oid}: {status_line}\n\n📍 {drop}\n"
            + (f"Pin: {pin}\n" if pin else "") + f"👤 {who}\n💰 {money}\n\n"
            f"Open the order: {kitchen_link}\nOr reply here with the Porter link and fare.")
    return send(text, f"<pre>{escape(porter.booking_text(order))}</pre>",
                relay={"kind": "order", "order_id": order["id"]},
                template=(KITCHEN_RIDER_V2, [oid, status_line, drop, who, money], oid),
                fallback_template=(KITCHEN_RIDER, [oid, f"{status_line}. Drop: {drop}. {who}. {money}"]))


_URL = re.compile(r"https?://\S+")
_FARE = re.compile(r"(?:₹|\brs\.?|\binr|\bfare)\s*:?\s*(\d{2,4}(?:\.\d{1,2})?)", re.I)
_ORDER_REF = re.compile(r"#\s*(\d{1,7})\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{4}[\s-]?\d{5})(?!\d)")


def looks_like_tracking_link(text: str) -> bool:
    """A courier link and/or a fare ("₹86", "Rs 86", "fare 86") from the owner."""
    return bool(extract_tracking_url(text or "")["url"] or _FARE.search(text or ""))


def _fare(text: str) -> float | None:
    m = _FARE.search(_URL.sub(" ", text or ""))
    return float(m.group(1)) if m else None


def handle_admin_tracking_link(text: str) -> str:
    """the owner sent a courier link and/or the fare: attach it to the right order.

    Link (+ optional fare): the waiting delivery order (preparing/ready, no
    rider yet) — "#<id>" picks one when several wait. Fare alone: the order
    that has a rider but no fare yet. Returns the reply for the owner."""
    from . import db, orders
    found = extract_tracking_url(text)
    url = found["url"] if found["state"] in ("ok", "unknown") else ""
    fare = _fare(text)
    ref = _ORDER_REF.search(text)
    today = [o for o in db.today_orders() if o.get("order_type") == "delivery"]
    if url:
        pool = [o for o in today if o.get("status") in ("preparing", "ready")
                and not (o.get("sr_courier") or o.get("sr_tracking_url"))]
        none_msg = "No delivery order is waiting for a rider right now."
    else:
        pool = [o for o in today if (o.get("sr_courier") or o.get("sr_tracking_url"))
                and not (o.get("delivery_fee_final") or o.get("courier_cost"))
                and o.get("status") != "cancelled"]
        none_msg = "No booked order is waiting for its fare. Send the Porter link with the fare."
    if ref:
        target = next((o for o in pool if o["id"] == int(ref.group(1))), None)
        if not target:
            return f"Order #{ref.group(1)} isn't waiting for that (already done, or not a delivery)."
    elif len(pool) == 1:
        target = pool[0]
    elif not pool:
        return none_msg
    else:
        ids = ", ".join(f"#{o['id']}" for o in pool)
        return f"Which order is this for? Waiting: {ids}. Send it again with the number, e.g. #{pool[0]['id']}."

    from .notify import notify_dispatch, notify_delivery_fee
    try:
        if url:
            phone = _PHONE.search(_URL.sub(" ", text))
            result = orders.record_manual_dispatch(
                target["id"], url, "", re.sub(r"\D", "", phone.group(1)) if phone else "", fare)
        else:
            orders.record_delivery_fee(target["id"], fare)
    except orders.OrderError as e:
        return f"Couldn't save it: {e.message}"
    try:
        o = db.get_order(target["id"])
        if url:
            notify_dispatch(o, result)
        elif fare and orders.is_fee_later(o):
            notify_delivery_fee(o)
    except Exception:
        log.exception("customer notification failed for order %s", target["id"])
    fee_later = orders.is_fee_later(db.get_order(target["id"]) or target)
    if url and not fare:
        why = "so the customer can pay it" if fee_later else "so we can keep track of delivery costs"
        return (f"✅ Order #{target['id']} is out for delivery and the customer has the link. "
                f"What did Porter charge? Reply e.g. \"#{target['id']} ₹86\" {why}.")
    if url:
        extra = f" and the ₹{fare:g} delivery charge" if fee_later else ""
        return f"✅ Order #{target['id']} is out for delivery. Customer has the link{extra}."
    if fee_later:
        return f"✅ Delivery ₹{fare:g} saved for order #{target['id']}. The customer has been asked to pay it."
    return f"✅ Porter fare ₹{fare:g} noted for order #{target['id']}."
