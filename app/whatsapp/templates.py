"""Every WhatsApp template the app sends: exact wording, and sync with Meta.

This file is the single source of truth for the business-started messages
(order updates to customers, alerts to the kitchen). Each definition holds
the header, body ({{1}}..{{n}} filled by the code, in order), footer, Meta's
review examples and an optional URL button whose link ends in the order's
tracking token.

`sync()` makes Meta match this file: it creates missing templates, edits
ones whose wording changed (Meta re-reviews an edit, usually in minutes) and
sets the chat's welcome message, ice breakers and / commands. It runs at
startup and from POST /api/admin/whatsapp/templates/sync; GET
/api/admin/whatsapp/templates shows the result next to Meta's status.

Meta's rules, all respected below: UTILITY category for transactional
messages; a body must not start or end with a variable, and variables need
words around them; headers have no emoji or formatting (60 chars max);
footers 60 chars max; variable values carry no newlines/tabs.
"""
import logging
import os
import re

from ..business import business

log = logging.getLogger("whatsapp.templates")

LANGUAGE = "en"
SITE = business.site_url
TRACK_URL = SITE + "/track/"
TRACK_EXAMPLE = TRACK_URL + "K3X9P2QA"
FOOTER = f"{business.name} · pure veg · {business.locality}, {business.city}"
KITCHEN_FOOTER = "Swipe right on this message to reply"

ORDER_PLACED = "order_placed"
ORDER_ACCEPTED = "order_accepted"
READY_DELIVERY = "order_ready_delivery"
READY_PICKUP = "order_ready_pickup"
RIDER_ON_THE_WAY = "rider_on_the_way"
DELIVERED = "order_delivered"
DELIVERED_RATE = "order_delivered_rate"
# Delivered + one-tap feedback (quick replies send FB:<good|ok|bad>:<order id>).
FEEDBACK = "order_feedback"
FEEDBACK_CHOICES = [("good", "Loved it"), ("ok", "It was okay"), ("bad", "Not great")]  # Meta rejects emoji in button text
# Our templates Meta has put in a paid category other than UTILITY (it did this
# to kitchen_alert). Filled by sync(); notify skips these for customers.
NOT_UTILITY: set[str] = set()
CANCELLED = "order_cancelled"
PAYMENT_RECEIVED = "payment_received"
# Kitchen alerts tied to one order / one customer message, so Meta keeps them
# UTILITY (it re-filed the open-ended kitchen_alert as MARKETING:
# ~7x the price and subject to marketing caps). The generic one is a fallback.
KITCHEN_RIDER = "kitchen_book_rider"
# Oct 2026: one short line per detail and an Open order button (the old one
# packed the whole card into a single variable, which WhatsApp shows as one line).
KITCHEN_RIDER_V2 = "kitchen_rider_v2"
KITCHEN_MESSAGE = "kitchen_customer_message"
# KITCHEN_WA_TEMPLATE may override the name, but only with a valid template
# name (Railway once held the template's body text here, which Meta rejects).
_kitchen_env = (os.environ.get("KITCHEN_WA_TEMPLATE") or "").strip()
KITCHEN_ALERT = _kitchen_env if re.fullmatch(r"[a-z0-9_]{1,512}", _kitchen_env) else "kitchen_alert"

DEFINITIONS = {
    ORDER_PLACED: {
        "used_for": "customer, the moment the order is placed",
        "header": "Order received",
        "body": (f"Hi {{{{1}}}}, thank you for ordering from {business.name}! 🙏\n\n"
                 "*Order #{{2}}*\n{{3}}\n*Total: ₹{{4}}*\n⏱ {{5}}\n\n"
                 "We'll message you the moment the kitchen accepts it."),
        "example": ["Asha", "142", "1 North Indian Thali, 2 Masala Chai", "367",
                    "Arriving in about 45–60 minutes"],
        "button": "Track your order",
    },
    ORDER_ACCEPTED: {
        "used_for": "customer, kitchen accepted the order on the POS",
        "header": "We're cooking your order",
        "body": ("Good news, {{1}}! The kitchen has accepted *order #{{2}}* "
                 "and it's on the stove now 🍳\n\nWe'll tell you as soon as it's packed."),
        "example": ["Asha", "142"],
        "button": "Track your order",
    },
    READY_DELIVERY: {
        "used_for": "customer, food packed (delivery order)",
        "header": "Packed and ready",
        "body": ("Hi {{1}}, your *order #{{2}}* is packed and ready 🛍️\n\n"
                 "We're booking your rider now. You'll get the rider's live tracking link next."),
        "example": ["Asha", "142"],
        "button": "Track your order",
    },
    READY_PICKUP: {
        "used_for": "customer, food packed (pickup order)",
        "header": "Ready for pickup",
        "body": ("Hi {{1}}, your *order #{{2}}* is ready to collect 🛍️\n\n"
                 "📍 {{3}}\n\nJust show this message at the counter."),
        "example": ["Asha", "142", business.pickup_line],
        "button": "Track your order",
    },
    RIDER_ON_THE_WAY: {
        "used_for": "customer, rider booked and on the way",
        "header": "On its way",
        "body": ("Hi {{1}}, your *order #{{2}}* is on its way with a Porter rider 🛵\n\n"
                 "Follow the rider live: {{3}}\n\n"
                 "Please keep your phone handy in case the rider calls."),
        "example": ["Asha", "142", "https://porter.in/track/AB12CD"],
        "button": "Track your order",
    },
    DELIVERED: {
        "used_for": "customer, delivered (fallback without the rating button)",
        "header": "Delivered",
        "body": ("Hi {{1}}, your *order #{{2}}* has been delivered. Enjoy your meal! 🍛\n\n"
                 "If anything wasn't right, just reply here and we'll fix it."),
        "example": ["Asha", "142"],
        "button": None,
    },
    FEEDBACK: {
        "used_for": "customer, delivered, with one-tap feedback buttons",
        "header": "Delivered",
        "body": ("Hi {{1}}, your *order #{{2}}* has been delivered. Enjoy your meal! 🍛\n\n"
                 "How was it? One tap tells the kitchen."),
        "example": ["Asha", "142"],
        "button": None,
        "quick_replies": [label for _, label in FEEDBACK_CHOICES],
    },
    DELIVERED_RATE: {
        "used_for": "customer, delivered, with a button to rate each dish",
        "header": "Delivered",
        "body": ("Hi {{1}}, your *order #{{2}}* has been delivered. Enjoy your meal! 🍛\n\n"
                 "How was it? Tap below to rate each dish. It takes ten seconds "
                 "and helps a small family kitchen a lot."),
        "example": ["Asha", "142"],
        "button": "Rate your food",
    },
    CANCELLED: {
        "used_for": "customer, order cancelled",
        "header": "Order cancelled",
        "body": ("Hi {{1}}, your *order #{{2}}* has been cancelled.\n\n{{3}}\n\n"
                 "Sorry about that. Reply here if you have any questions."),
        "example": ["Asha", "142", "Your UPI payment of ₹367 will be refunded within 2 days."],
        "button": None,
    },
    PAYMENT_RECEIVED: {
        "used_for": "customer, kitchen confirmed the UPI payment",
        "header": "Payment received",
        "body": ("Thank you, {{1}}! We've received ₹{{2}} for *order #{{3}}* ✅\n\n"
                 "Your food is in good hands."),
        "example": ["Asha", "367", "142"],
        "button": "Track your order",
    },
    KITCHEN_RIDER: {
        "used_for": "kitchen (the owner): order accepted, book the Porter rider",
        "header": "Book a rider",
        "body": ("Order #{{1}} has been accepted on the POS and needs a Porter rider.\n\n"
                 "{{2}}\n\n"
                 "Once booked, reply to this message with the Porter link and the fare."),
        "example": ["142", "Drop: Asha, 5 Example Street. Phone 98123 45678. Prepaid, collect nothing."],
        "button": None,
        "footer": KITCHEN_FOOTER,
    },
    KITCHEN_RIDER_V2: {
        "used_for": "kitchen (the owner): delivery order needs a Porter rider",
        "header": "Book a Porter rider",
        "body": ("Order #{{1}}: {{2}}\n\n"
                 "📍 {{3}}\n"
                 "👤 {{4}}\n"
                 "💰 {{5}}\n\n"
                 "Tap Open order to copy the details into Porter and send the rider link."),
        "example": ["142", "accepted, book the rider now",
                    "5 Example Street (near the temple tank)",
                    "Asha · 98123 45678", "Paid by UPI. Rider collects nothing."],
        "button": "Open order",
        "button_url": SITE + "/kitchen/o/",
        "button_example": SITE + "/kitchen/o/142",
        "footer": "Or swipe right and send the Porter link and fare",
    },
    KITCHEN_MESSAGE: {
        "used_for": "kitchen (the owner): a customer's question or file, to answer by replying",
        "header": "Customer message",
        "body": ("Message from customer {{1}}:\n\n{{2}}\n\n"
                 "Reply to this message to answer them on WhatsApp."),
        "example": ["Asha (+91 98123 45678), order #142", "Can you make it less spicy please?"],
        "button": None,
        "footer": KITCHEN_FOOTER,
    },
    KITCHEN_ALERT: {
        "used_for": "kitchen (the owner): anything else (fallback; Meta files it as marketing)",
        "header": "Kitchen alert",
        "body": "🔔 New for the kitchen: {{1}}\n\nReply to this message to answer or update it.",
        "example": ["Order #142 accepted. Book a Porter 2-wheeler: drop Asha, 5 Example Street, prepaid."],
        "button": None,
        "footer": KITCHEN_FOOTER,
    },
}

# What the checker compares against Meta.
EXPECTED = {name: {"params": d["body"].count("{{"), "url_button": bool(d.get("button")),
                   "used_for": d["used_for"]} for name, d in DEFINITIONS.items()}

# The chat's front door (Meta "conversational components"): a welcome
# message event when someone opens the chat for the first time, up to 4
# ice-breaker buttons, and / commands.
ICE_BREAKERS = ["Show me the menu", "Track my order", "Reorder my last order", "Talk to the kitchen"]
COMMANDS = [
    {"command_name": "menu", "command_description": "See today's menu and order"},
    {"command_name": "track", "command_description": "Where is my order?"},
    {"command_name": "reorder", "command_description": "Order your last order again"},
    {"command_name": "kitchen", "command_description": "Ask the kitchen a question"},
]


def components(name: str) -> list[dict]:
    d = DEFINITIONS[name]
    comps = []
    if d.get("header"):
        comps.append({"type": "HEADER", "format": "TEXT", "text": d["header"][:60]})
    comps.append({"type": "BODY", "text": d["body"], "example": {"body_text": [d["example"]]}})
    comps.append({"type": "FOOTER", "text": (d.get("footer") or FOOTER)[:60]})
    if d.get("quick_replies"):
        comps.append({"type": "BUTTONS", "buttons": [
            {"type": "QUICK_REPLY", "text": t[:25]} for t in d["quick_replies"]]})
    elif d.get("button"):
        comps.append({"type": "BUTTONS", "buttons": [{
            "type": "URL", "text": d["button"][:25], "url": d.get("button_url", TRACK_URL) + "{{1}}",
            "example": [d.get("button_example", TRACK_EXAMPLE)]}]})
    return comps


def create_payload(name: str) -> dict:
    return {"name": name, "language": LANGUAGE, "category": "UTILITY",
            "components": components(name)}


def _fingerprint(comps: list[dict]) -> tuple:
    """What matters for 'is Meta's copy the same as ours'."""
    by = {c.get("type", "").upper(): c for c in comps}
    btns = by.get("BUTTONS", {}).get("buttons", [])
    return (by.get("HEADER", {}).get("text", ""), by.get("BODY", {}).get("text", ""),
            by.get("FOOTER", {}).get("text", ""),
            tuple((b.get("text", ""), b.get("url", "")) for b in btns))


def _shape(comps: list[dict]) -> tuple[int, bool]:
    body = next((c for c in comps if c.get("type") == "BODY"), {})
    n = body.get("text", "").count("{{")
    url_var = any(
        "{{" in (b.get("url") or "")
        for c in comps if c.get("type") == "BUTTONS"
        for b in c.get("buttons", []) if b.get("type") == "URL"
    )
    return n, url_var


def check(meta_templates: list[dict]) -> list[dict]:
    """One row per template the app sends: found?, approved?, same wording
    as the code?, and does Meta's variable count match what the code sends."""
    rows = []
    for name, want in EXPECTED.items():
        found = [t for t in meta_templates if t.get("name") == name]
        approved = [t for t in found if t.get("status") == "APPROVED"]
        row = {"name": name, "used_for": want["used_for"], "code_sends": want["params"],
               "found": bool(found), "approved": bool(approved),
               "languages": sorted({t.get("language") for t in found}),
               "status": sorted({t.get("status") for t in found})}
        if found:
            row["wording_matches_code"] = (_fingerprint(found[0].get("components", []))
                                           == _fingerprint(components(name)))
        if approved:
            n, url_var = _shape(approved[0].get("components", []))
            row["template_has"] = n
            row["url_button_variable"] = url_var
            row["ok"] = n == want["params"] and url_var == want["url_button"]
        else:
            row["ok"] = False
        rows.append(row)
    return rows


_last_sync: dict = {}


def sync() -> dict:
    """Make Meta match DEFINITIONS (create / edit) and set the chat's welcome,
    ice breakers and commands. Never raises; returns and remembers the result."""
    global _last_sync
    from . import client
    result: dict = {"created": [], "updated": [], "unchanged": [], "failed": {}}
    try:
        waba = client.waba_id()
        if not waba:
            result["error"] = "WhatsApp Business Account id not found (set WHATSAPP_WABA_ID)"
            return result
        result["waba_id"] = waba
        have = {t.get("name"): t for t in client.list_templates(waba) if t.get("language") == LANGUAGE}
        NOT_UTILITY.clear()
        NOT_UTILITY.update(n for n, t in have.items() if n in DEFINITIONS and t.get("category") != "UTILITY")
        result["not_utility"] = sorted(NOT_UTILITY)
        for name in DEFINITIONS:
            try:
                meta = have.get(name)
                if not meta:
                    client.create_template(waba, create_payload(name))
                    result["created"].append(name)
                elif _fingerprint(meta.get("components", [])) != _fingerprint(components(name)):
                    if meta.get("status") in ("PENDING", "IN_APPEAL"):
                        result["unchanged"].append(f"{name} (in review, edit later)")
                        continue
                    client.edit_template(meta["id"], components(name))
                    result["updated"].append(name)
                else:
                    result["unchanged"].append(f"{name} ({meta.get('status')})")
            except Exception as e:  # one bad template must not stop the rest
                result["failed"][name] = str(e)[:300]
        try:
            client.set_conversational_automation(ICE_BREAKERS, COMMANDS)
            result["conversational_automation"] = "set"
        except Exception as e:
            result["conversational_automation"] = f"failed: {str(e)[:200]}"
    except Exception as e:
        result["error"] = str(e)[:300]
    finally:
        _last_sync = result
        log.info("WhatsApp template sync: %s", result)
    return result


def last_sync() -> dict:
    return _last_sync
