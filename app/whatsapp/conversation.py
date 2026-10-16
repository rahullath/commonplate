"""WhatsApp ordering bot: catalogue first, buttons everywhere, no typing.

Pure logic: `handle(...)` returns a list of outbound message dicts; the
webhook layer sends them (app/whatsapp/client.send_outbound). No network
calls here, so the whole flow runs in tests and scripts/whatsapp_repl.py.

The flow (Oct 2026):
  hi                 -> the menu as a list of sections; each opens a tappable
                        product list. Typing a dish name searches.
  cart from catalogue -> cart summary, Delivery / Pickup
  Delivery           -> address form (India) or typed address, then a
                        location pin, which sets the fixed fee band
  When               -> As soon as possible, or a lunch/dinner window
  Pay                -> UPI or Cash
  Summary            -> Place order -> orders.create_order (POS, kitchen,
                        WhatsApp confirmation template), UPI pay button
Typed words still work at any point: MENU, CART, TRACK, REORDER, HELP, CANCEL.

Message dicts:
  {"type": "text", "text"}
  {"type": "buttons", "text", "buttons": [(id, title) | title, ...]}  (max 3)
  {"type": "list", "text", "button", "sections": [{"title", "rows": [{"id", "title", "description"?}]}]}
  {"type": "catalog", "text"}                      whole menu, native catalogue view
  {"type": "products", "header", "text", "sections": [{"title", "ids": [...]}]}
  {"type": "location_request", "text"}
  {"type": "address", "text", "values": {...}}      India address form
  {"type": "cta", "text", "label", "url"}           one link button
"""
from datetime import datetime, timedelta, timezone

from .. import db, menu, orders
from ..business import business
from ..config import DELIVERY_RADIUS_KM, DELIVERY_ZONES
from . import sessions

SITE = business.site_url
PICKS_LIMIT = 30          # WhatsApp product_list maximum
PICKS_PER_SECTION = 6

STATUS_LABEL = {
    "new": "placed, waiting for the kitchen to accept it",
    "preparing": "being cooked",
    "ready": "packed and ready",
    "out_for_delivery": "on its way",
    "delivered": "delivered",
    "cancelled": "cancelled",
}

HELP_TEXT = (
    "Tap *Order now* to open the menu, or type a dish name to search here.\n"
    "You can also type MENU, TRACK, REORDER or CANCEL any time."
)


# ---------------------------------------------------------------- builders

def text(body: str) -> dict:
    return {"type": "text", "text": body}


def buttons(body: str, btns: list) -> dict:
    return {"type": "buttons", "text": body, "buttons": btns}


def money(n) -> str:
    v = round(float(n), 2)
    return "₹" + (str(int(v)) if v == int(v) else f"{v:.2f}")


def qty_disp(q) -> str:
    f = float(q)
    return str(int(f)) if f == int(f) else str(f)


def _is_indian(wa_id: str) -> bool:
    d = "".join(ch for ch in wa_id if ch.isdigit())
    return len(d) == 10 or (len(d) == 12 and d.startswith("91"))


def _phone_display(wa_id: str) -> str:
    d = "".join(ch for ch in wa_id if ch.isdigit())
    return "+" + d if len(d) > 10 else "+91" + d


# ---------------------------------------------------------------- menu

MENU_BODY = ("Tap *Order now* to open our menu 🍛 Search any dish, browse by section, add to "
             "cart and check out. Your number is already filled in, and order updates come "
             "right here on WhatsApp.")


def full_catalogue(body: str | None = None) -> dict:
    """Every dish in WhatsApp's own catalogue view (one long scroll)."""
    return {"type": "catalog", "text": body or (
        "The full menu 🍛 Add what you like, then tap the cart and *Send*.")}


def catalog_message(body: str | None = None) -> dict:
    """The menu: an *Order now* button opening the web menu signed in with
    this WhatsApp number (made into a cta in handle(), which knows wa_id)."""
    return {"type": "menu_link", "text": f"{body}\n\n{MENU_BODY}" if body else MENU_BODY}


def _menu_link(msg: dict, wa_id: str) -> dict:
    from .. import wa_link
    return {"type": "cta", "text": msg["text"], "label": "Order now", "url": wa_link.url(wa_id)}


def picks_message() -> dict | None:
    """Popular dishes available today, grouped by category, as a tappable
    product list (built at send time, so sold-out dishes never show)."""
    sections, total = [], 0
    for g in menu.grouped():
        items = [it for it in g["items"] if it["available"] and it.get("popular")]
        items = items[:PICKS_PER_SECTION]
        if not items or len(sections) == 10:
            continue
        items = items[: PICKS_LIMIT - total]
        if not items:
            break
        sections.append({"title": g["group"][:24], "ids": [it["id"] for it in items]})
        total += len(items)
    if not sections:
        return None
    return {"type": "products", "header": f"Popular at {business.name}"[:60],
            "text": "Our most-ordered dishes that are available today.",
            "sections": sections}


def _closed_now() -> bool:
    try:
        return bool(orders._schedule_closed_reason()) or not db.get_store_status().get("is_open", True)
    except Exception:
        return False


KITCHEN_PHONE = business.phone_local
CHECKOUT_STATES = {"type", "checkout_flow", "address", "location", "when", "payment", "confirm"}


def _ask_kitchen(sess: dict) -> list[dict]:
    sess["state"] = "ask_kitchen"
    return [text("Sure! Type your question or request and I'll pass it straight to the kitchen. "
                 f"They usually reply within a few minutes.\n\nUrgent? Call {KITCHEN_PHONE}.")]


def _relay(sess: dict, raw: str) -> list[dict]:
    """Free text that isn't part of ordering goes to the owner; she swipe-replies."""
    sess["state"] = "root"
    return [{"type": "relay", "text": raw},
            buttons("Got it 👍 I've passed your message to the kitchen. They'll reply right here.\n\n"
                    f"Urgent? Call {KITCHEN_PHONE}.",
                    [("MENU", "See the menu"), ("TRACK", "Track order")])]


def category_list(body: str = MENU_BODY) -> dict | None:
    """The menu as a WhatsApp list: one row per section with what's
    available today, so nobody scrolls 150 dishes. Rows are CAT_<index>."""
    rows = []
    for i, g in enumerate(menu.grouped()):
        avail = [it for it in g["items"] if it["available"]]
        full = [it for it in avail if not it["id"].endswith(menu.HALF_SUFFIX)]
        if not full:
            continue
        prices = [float(it["price"]) for it in avail]
        rows.append({"id": f"CAT_{i}", "title": g["group"][:24],
                     "description": f"{len(full)} dishes · {money(min(prices))}–{money(max(prices))}"})
    if not rows:
        return None
    return {"type": "list", "text": body, "button": "See sections",
            "sections": [{"title": "Today's menu", "rows": rows[:10]}]}


def _feed_id(item_id: str) -> str:
    # Half portions are <id>-half in the catalogue feed (app/catalog.py).
    if item_id.endswith(menu.HALF_SUFFIX):
        return item_id[: -len(menu.HALF_SUFFIX)] + "-half"
    return item_id


def product_lists(title: str, items: list[dict], body: str) -> list[dict]:
    """Dishes as tappable product lists, sectioned by category. WhatsApp
    shows at most 30 products per message, so long lists are split evenly;
    a dish and its half portion stay together. Everything goes in one cart."""
    ids = {it["id"] for it in items}
    ordered = []  # (category, feed id)
    for it in items:
        iid = it["id"]
        if iid.endswith(menu.HALF_SUFFIX) and iid[: -len(menu.HALF_SUFFIX)] in ids:
            continue  # added right after its full portion
        ordered.append((it.get("category") or title, _feed_id(iid)))
        if iid + menu.HALF_SUFFIX in ids:
            ordered.append((it.get("category") or title, _feed_id(iid + menu.HALF_SUFFIX)))
    if not ordered:
        return []
    parts = -(-len(ordered) // PICKS_LIMIT)
    size = -(-len(ordered) // parts)
    out = []
    for n in range(parts):
        chunk = ordered[n * size:(n + 1) * size]
        sections: list[dict] = []
        for cat, pid in chunk:
            if not sections or sections[-1]["title"] != cat[:24]:
                if len(sections) == 10:
                    sections[-1]["ids"].append(pid)
                    continue
                sections.append({"title": cat[:24], "ids": []})
            sections[-1]["ids"].append(pid)
        head = title if parts == 1 else f"{title} ({n + 1}/{parts})"
        out.append({"type": "products", "header": head[:60], "text": body, "sections": sections})
    return out


def category_products(index: int) -> list[dict]:
    """One menu section as tappable product lists."""
    groups = menu.grouped()
    if not 0 <= index < len(groups):
        return [catalog_message()]
    g = groups[index]
    items = sorted((it for it in g["items"] if it["available"]),
                   key=lambda it: it.get("category") or "")  # stable: popular first within each
    out = product_lists(g["group"], items,
                        "Tap a dish to add it to your cart. When you're done, open the cart and "
                        "*Send*. Want another section? Type *browse*.")
    return out or [text(f"{g['group']} is finished for today."), catalog_message()]


SEARCH_SKIP = {"the", "and", "you", "have", "want", "with", "for", "please", "pls", "plz", "can",
               "get", "any", "some", "one", "two", "three", "order", "need", "give", "today",
               "send", "from", "what", "your", "there", "this", "that", "like", "also", "more"}


def search_menu(query: str) -> list[dict]:
    """Available dishes matching a typed query ('paneer', 'aloo paratha'),
    best matches first. Matches names, other names (aloo/potato…) and
    categories by word prefix, so 'parathas' finds 'Aloo Paratha'."""
    import re
    from ..seo_aliases import aliases_for
    words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) >= 3 and w not in SEARCH_SKIP]
    if not words:
        return []
    scored = []
    for it in menu.load_menu():
        if not it.get("available", True):
            continue
        hay = " ".join([it["name"], it.get("category") or "", it.get("group") or "",
                        *aliases_for(it["id"])]).lower()
        hay_words = set(re.findall(r"[a-z]+", hay))
        score = sum(1 for w in words
                    if any(h.startswith(w) or (len(h) >= 4 and w.startswith(h)) for h in hay_words))
        if score:
            scored.append((score, it))
    if not scored:
        return []
    best = max(sc for sc, _ in scored)
    hits = [it for sc, it in scored if sc == best]
    hits.sort(key=lambda it: (not it.get("popular"), it["name"]))
    hits = hits[:PICKS_LIMIT]
    return sorted(hits, key=lambda it: it.get("category") or "")  # one section per category


def _search(raw: str) -> list[dict] | None:
    hits = search_menu(raw)
    if not hits:
        return None
    return product_lists(f"Results for “{raw[:40]}”", hits,
                         "Tap a dish to add it to your cart, then open the cart and *Send*. "
                         "Not what you meant? Type another dish, or *kitchen* to ask us.")[:1]


def _welcome(sess: dict) -> list[dict]:
    sess["state"] = "root"
    out = []
    name = (sess["ctx"].get("profile_name") or "").split(" ")[0]
    if not sess["ctx"].get("seen"):
        sess["ctx"]["seen"] = True
        hello = f"Hello{' ' + name if name else ''}! Welcome to {business.name} 🍛"
        out.append(text(hello + f"\n{business.tagline}. Order right here, no app needed."))
    special = db.get_special()
    if special:
        out.append(text(f"Today's special: {special['item_name']} — {money(special['price'])} ✨"))
    if _closed_now():
        out.append(catalog_message(
            "We're closed right now, but you can order ahead for the next lunch or dinner."))
    else:
        out.append(catalog_message())
    extra = []
    if db.customer_orders(sess["wa_id"], limit=1):
        extra.append(("REORDER", "Reorder last"))
    extra += [("BROWSE", "Order in WhatsApp"), ("PICKS", "Popular dishes")]
    out.append(buttons("Prefer to stay in WhatsApp? Browse the menu here, or type a dish name "
                       "like _paneer_.", extra[:3]))
    return out


# ---------------------------------------------------------------- cart

def cart_summary(sess: dict) -> tuple[str, float]:
    lines, total = [], 0.0
    for item_id, qty in sess["cart"].items():
        m = menu.get_item(item_id)
        if not m:
            continue
        total += m["price"] * qty
        lines.append(f"{qty_disp(qty)} × {m['name']} — {money(m['price'] * qty)}")
    if not lines:
        return "Your cart is empty.", 0.0
    return "\n".join(lines) + f"\n*Subtotal: {money(total)}*", total


def _catalog_order(sess: dict, items: list[dict]) -> list[dict]:
    """A cart sent from the catalogue replaces the chat cart and starts
    checkout. Dishes finished for today are left out."""
    cart, skipped = {}, []
    for it in items:
        pid = str(it.get("id") or "")
        if pid.endswith("-half"):  # the feed's half SKU id (app/catalog.py)
            pid = pid[: -len("-half")] + menu.HALF_SUFFIX
        m = menu.get_item(pid)
        if not m:
            continue
        if not menu.is_available(m["id"]):
            skipped.append(m["name"])
            continue
        cart[m["id"]] = cart.get(m["id"], 0) + max(1, int(it.get("qty") or 1))
    if not cart:
        sess["state"] = "root"
        why = f"{', '.join(skipped)} {'is' if len(skipped) == 1 else 'are'} finished for today. " if skipped else ""
        return [text(f"Sorry, {why}Pick something else from the menu."), catalog_message()]
    sess["cart"] = cart
    out = [text(f"Finished for today, so left out: {', '.join(skipped)}.")] if skipped else []
    return out + _ask_order_type(sess)


def _ask_order_type(sess: dict) -> list[dict]:
    """After the cart: one checkout form (WhatsApp Flow) when configured,
    else Delivery / Pickup buttons and the step-by-step questions."""
    from ..config import DELIVERY_ZONES, WHATSAPP_CHECKOUT_FLOW_ID, WHATSAPP_FLOW_MODE
    body, _ = cart_summary(sess)
    sess["state"] = "type"
    steps = buttons(f"Your order:\n{body}\n\nDelivery or pickup?",
                    [("DELIVERY", "Delivery"), ("PICKUP", "Pickup"), ("EDIT", "Change cart")])
    slots = [{"id": r["id"], "title": r["title"][:30], "description": r.get("description", "")[:300]}
             for r in _window_options()]
    if not (WHATSAPP_CHECKOUT_FLOW_ID and slots):
        return [steps]
    saved = db.get_customer(sess["wa_id"]) or {}
    fees = ", ".join(f"₹{z['fee']} up to {z['max_km']:.0f} km" for z in DELIVERY_ZONES)
    sess["state"] = "checkout_flow"
    return [{"type": "flow", "flow_id": WHATSAPP_CHECKOUT_FLOW_ID, "mode": WHATSAPP_FLOW_MODE,
             "header": "Your order", "cta": "Checkout", "screen": "CHECKOUT",
             "token": f"checkout-{sess['wa_id']}",
             "text": f"{body}\n\nTap *Checkout* to choose delivery, time and payment in one go. "
                     "Want to change the dishes? Edit the cart and send it again.",
             "data": {"summary": body.replace("*", ""), "slots": slots,
                      "saved_address": (saved.get("address") or "")[:300],
                      "saved_pincode": (saved.get("pincode") or "")[:6],
                      "fees": f"Fixed fee: {fees}"},
             "fallback": steps}]


def _on_checkout_form(sess: dict, f: dict) -> list[dict]:
    """The checkout Flow was submitted: apply everything, place the address
    on the map for the fee band, then show the summary to confirm."""
    if not sess["cart"]:
        return [text("Your cart is empty now. Send a cart from the menu first."), catalog_message()]
    ctx = sess["ctx"]
    ctx["order_type"] = "pickup" if f.get("order_type") == "pickup" else "delivery"
    ctx["payment"] = "COD" if f.get("payment") == "cod" else "UPI"
    if (f.get("note") or "").strip():
        ctx["note"] = f["note"].strip()[:300]
    if not _apply_when(ctx, f.get("when") or ""):
        return _ask_when(sess)
    if ctx["order_type"] == "pickup":
        ctx["fee"] = 0
        return _summary(sess)
    ctx["address"] = (f.get("address") or "").strip()[:300]
    ctx["pincode"] = (f.get("pincode") or "").strip()[:6] or None
    from .. import geo
    point = geo.geocode(ctx["address"], ctx.get("pincode") or "")
    if not point or not point.get("precise"):
        # Only an area match: the rider needs the exact spot (it goes on the
        # Porter card), so ask for the pin. One tap in WhatsApp.
        return [text("Got it! One last thing: share your location pin so the rider finds "
                     "your door. It also sets the delivery fee.")] + _ask_location(sess)
    return _set_location(sess, point["lat"], point["lng"])


# ---------------------------------------------------------------- address

def _ask_address(sess: dict) -> list[dict]:
    sess["state"] = "address"
    ctx = sess["ctx"]
    saved = db.get_customer(sess["wa_id"]) or {}
    if saved.get("address") and not ctx.get("asked_saved"):
        ctx["asked_saved"] = True
        ctx["saved_address"] = saved["address"]
        short = saved["address"][:300]
        return [buttons(f"Deliver to your last address?\n{short}",
                        [("SAVED", "Yes, same address"), ("NEWADDR", "New address")])]
    if _is_indian(sess["wa_id"]):
        return [{"type": "address", "text": "Where should we deliver? Fill in your address below.",
                 "values": {"name": ctx.get("profile_name") or "",
                            "phone_number": _phone_display(sess["wa_id"])}}]
    return [text("Please type your full delivery address: house/flat, street, area, landmark.")]


def _address_from_form(values: dict) -> str:
    parts = [values.get(k) for k in ("house_number", "floor_number", "tower_number",
                                     "building_name", "address", "landmark_area", "city")]
    line = ", ".join(p.strip() for p in parts if p and str(p).strip())
    pin = (values.get("in_pin_code") or "").strip()
    return f"{line} {pin}".strip()


def _ask_location(sess: dict) -> list[dict]:
    sess["state"] = "location"
    return [{"type": "location_request",
             "text": "Now share your location pin so the rider finds you. "
                     f"It also sets your delivery fee (₹{min(z['fee'] for z in DELIVERY_ZONES)}–₹{max(z['fee'] for z in DELIVERY_ZONES)} by distance)."}]


def _set_location(sess: dict, lat: str, lng: str) -> list[dict]:
    from ..delivery.estimate import estimate
    ctx = sess["ctx"]
    _, subtotal = cart_summary(sess)
    q = estimate(float(lat), float(lng), subtotal)
    if q.get("serviceable") is False:
        sess["state"] = "type"
        return [buttons(f"That pin is {q['km']:.1f} km away, outside our {DELIVERY_RADIUS_KM:g} km delivery area. "
                        f"You can pick it up from {business.locality} instead.",
                        [("PICKUP", "Pickup instead"), ("CANCEL", "Cancel order")])]
    ctx.update(lat=str(lat), lng=str(lng), fee=q["fee"], km=round(q["km"], 1))
    if ctx.get("when") and ctx.get("payment"):  # the checkout Flow already asked
        return _summary(sess)
    return _ask_when(sess)


# ---------------------------------------------------------------- time, payment

def _window_options(now: datetime | None = None) -> list[dict]:
    """ASAP (when open now) plus the next lunch/dinner windows that are
    still open for orders, as list rows."""
    from ..config import DELIVERY_WINDOWS
    now = now or datetime.now(orders.IST)
    rows = []
    if not orders._schedule_closed_reason() and db.get_store_status().get("is_open", True):
        rows.append({"id": "WHEN_NOW", "title": "As soon as possible",
                     "description": "About 45–60 min for delivery, 20 for pickup"})
    for day_off, day_name in ((0, "Today"), (1, "Tomorrow")):
        day = (now + timedelta(days=day_off)).date()
        for w in DELIVERY_WINDOWS:
            ref = datetime(day.year, day.month, day.day, *w["ref"], tzinfo=orders.IST)
            if ref < now + timedelta(minutes=45):
                continue
            iso = ref.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if orders._schedule_closed_reason(iso, w["id"]):
                continue
            rows.append({"id": f"WHEN_{w['id']}_{day.isoformat()}",
                         "title": f"{day_name} {w['label'].split()[0].lower()}"[:24],
                         "description": w["text"]})
    return rows[:10]


def _ask_when(sess: dict) -> list[dict]:
    rows = _window_options()
    if not rows:
        sess["state"] = "root"
        return [text("We're not taking orders for the next two days. Please try again later.")]
    sess["state"] = "when"
    return [{"type": "list", "text": "When would you like it?", "button": "Choose a time",
             "sections": [{"title": "Delivery / pickup time", "rows": rows}]}]


def _apply_when(ctx: dict, choice: str) -> bool:
    """WHEN_NOW or WHEN_<window>_<date> into ctx; False if not understood."""
    from ..config import DELIVERY_WINDOWS
    choice = (choice or "").upper()
    if choice == "WHEN_NOW":
        ctx.update(when="As soon as possible", scheduled_at=None, scheduled_window=None)
        return True
    try:
        _, win_id, day = choice.split("_", 2)
        w = next(x for x in DELIVERY_WINDOWS if x["id"] == win_id.lower())
        d = datetime.fromisoformat(day)
    except (ValueError, StopIteration):
        return False
    ref = datetime(d.year, d.month, d.day, *w["ref"], tzinfo=orders.IST)
    ctx.update(scheduled_at=ref.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               scheduled_window=w["id"])
    ctx["when"] = orders._window_label(w["id"], ctx["scheduled_at"])
    return True


def _set_when(sess: dict, choice: str) -> list[dict] | None:
    ctx = sess["ctx"]
    if not _apply_when(ctx, choice):
        return None
    sess["state"] = "payment"
    return [buttons("How would you like to pay?",
                    [("PAY_UPI", "UPI"), ("PAY_COD", "Cash on delivery" if ctx.get("order_type") == "delivery" else "Cash at pickup")])]


# ---------------------------------------------------------------- summary, place

def _totals(sess: dict) -> dict:
    _, subtotal = cart_summary(sess)
    packing = orders.packing_fee_for(subtotal)
    discount = 0.0
    if sess["ctx"].get("discount_code"):
        from .. import discounts
        try:
            discount = discounts.evaluate(sess["ctx"]["discount_code"],
                                          orders._normalize_phone(sess["wa_id"]), subtotal)["amount"]
        except discounts.DiscountError:
            sess["ctx"].pop("discount_code", None)  # cart changed below its minimum, etc.
    gst = orders.gst_for(subtotal - discount + packing)
    fee = float(sess["ctx"].get("fee") or 0) if sess["ctx"].get("order_type") == "delivery" else 0.0
    return {"subtotal": subtotal, "discount": discount, "packing": packing, "gst": gst, "fee": fee,
            "total": round(subtotal - discount + packing + gst + fee, 2)}


def _summary(sess: dict) -> list[dict]:
    ctx = sess["ctx"]
    body, _ = cart_summary(sess)
    t = _totals(sess)
    lines = [body]
    if t["discount"]:
        lines.append(f"Discount ({ctx['discount_code']}): −{money(t['discount'])}")
    if t["packing"]:
        lines.append(f"Packing: {money(t['packing'])}")
    if t["gst"]:
        lines.append(f"GST: {money(t['gst'])}")
    if ctx.get("order_type") == "delivery":
        lines.append(f"Delivery ({ctx.get('km', '?')} km): {money(t['fee'])}")
    lines.append(f"*Total: {money(t['total'])}*")
    lines.append("")
    if ctx.get("order_type") == "delivery":
        lines.append(f"📍 {ctx.get('address', '')}")
    else:
        lines.append(f"📍 Pickup: {business.pickup_line}")
    lines.append(f"🕒 {ctx.get('when')}")
    lines.append(f"💳 {'UPI' if ctx.get('payment') == 'UPI' else 'Cash'}")
    if ctx.get("note"):
        lines.append(f"📝 {ctx['note']}")
    else:
        lines.append("_Any request (less spicy, no onion)? Just type it._")
    sess["state"] = "confirm"
    return [buttons("\n".join(lines)[:1000],
                    [("PLACE", "Place order"), ("CHANGE", "Change details"), ("CANCEL", "Cancel")])]


def _try_code(sess: dict, raw: str) -> list[dict] | None:
    """A discount code typed at the summary ("TRIAL15" or "code TRIAL15").
    Anything that isn't a known code is left alone (it becomes a note)."""
    from .. import discounts
    words = raw.split()
    if len(words) == 2 and words[0].lower() in ("code", "coupon", "promo"):
        words = words[1:]
    if len(words) != 1 or not discounts.get(words[0]):
        return None
    _, subtotal = cart_summary(sess)
    try:
        d = discounts.evaluate(words[0], orders._normalize_phone(sess["wa_id"]), subtotal)
    except discounts.DiscountError as e:
        return [text(str(e))] + _summary(sess)
    sess["ctx"]["discount_code"] = d["code"]
    return [text(f"🎉 {d['code']} applied: {d['label']}.")] + _summary(sess)


FEEDBACK_LABEL = {"GOOD": "😋 Loved it", "OK": "🙂 It was okay", "BAD": "😕 Not great"}


def _on_feedback_tap(sess: dict, t: str) -> list[dict]:
    from ..config import GOOGLE_REVIEW_LINK
    parts = t.split(":")
    rating = parts[1] if len(parts) > 1 else ""
    oid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    o = db.get_order(oid) if oid else None
    if rating not in FEEDBACK_LABEL or not o or \
            orders._normalize_phone(o.get("customer_phone") or "") != orders._normalize_phone(sess["wa_id"]):
        return [text("Thank you! 🙏")]
    db.set_feedback(oid, rating.lower())
    if rating == "GOOD":
        sess["state"] = "root"
        return [text("That made the kitchen's day, thank you! 🙏"),
                {"type": "cta", "text": "If you have 20 seconds, a Google review helps a small family "
                                        "kitchen more than anything.",
                 "label": "Write a review", "url": GOOGLE_REVIEW_LINK}]
    sess["state"] = "feedback"
    sess["ctx"]["feedback_order"] = oid
    out = [text("Thanks for telling us. What could have been better? A few words is plenty.")]
    if rating == "BAD":  # the owner hears straight away, even if no words follow
        out.insert(0, {"type": "relay", "text": f"Order #{oid}: the customer tapped “😕 Not great”."})
    return out


def _feedback_comment(sess: dict, raw: str) -> list[dict]:
    oid = sess["ctx"].pop("feedback_order", None)
    sess["state"] = "root"
    if oid:
        db.set_feedback(oid, comment=raw[:1000])
        fb = db.get_feedback(oid) or {}
        label = FEEDBACK_LABEL.get((fb.get("rating") or "").upper(), "")
        relay = {"type": "relay", "text": f"Feedback on order #{oid} ({label}): {raw}"}
    else:
        relay = {"type": "relay", "text": raw}
    return [relay, text("Thank you. The kitchen has your note and may reply right here.")]


def _place_order(sess: dict) -> list[dict]:
    ctx = sess["ctx"]
    items = [{"item_id": i, "qty": q} for i, q in sess["cart"].items()]
    try:
        result = orders.create_order(
            phone=sess["wa_id"],
            name=ctx.get("name") or ctx.get("profile_name") or "Customer",
            address=ctx.get("address") if ctx.get("order_type") == "delivery" else None,
            order_type=ctx.get("order_type") or "delivery",
            pincode=ctx.get("pincode"),
            payment_method="upi" if ctx.get("payment") == "UPI" else "cod",
            scheduled_at=ctx.get("scheduled_at"),
            scheduled_window=ctx.get("scheduled_window"),
            discount_code=ctx.get("discount_code"),
            instructions=ctx.get("note"),
            items=items,
            lat=ctx.get("lat"),
            lng=ctx.get("lng"),
        )
    except orders.OrderError as e:
        sess["state"] = "root"
        return [text(e.message), catalog_message("Send a new cart from the menu any time.")]
    oid = result["order_id"]
    sess["ctx"]["last_order_id"] = oid
    sess["ctx"]["last_token"] = result.get("tracking_token")
    paying_upi = ctx.get("payment") == "UPI"
    _reset_checkout(sess)
    # The order confirmation itself is the order_placed template that
    # orders.create_order sends; here only what that message can't do.
    if paying_upi:
        pay = f"{SITE}/pay/{result.get('tracking_token') or oid}"
        return [{"type": "cta", "text": f"Order #{oid} is with the kitchen ✅\n\n"
                 f"Tap below to pay *{money(result['total'])}* with GPay, PhonePe, Paytm "
                 "or any UPI app. It opens your UPI app with everything filled in.",
                 "label": f"Pay {money(result['total'])}", "url": pay}]
    return [text(f"Order #{oid} is with the kitchen ✅ We'll message you at every step.")]


def _reset_checkout(sess: dict) -> None:
    sess["cart"] = {}
    sess["state"] = "root"
    for k in ("name", "address", "pincode", "saved_address", "asked_saved", "when",
              "scheduled_at", "scheduled_window", "payment", "order_type", "fee",
              "km", "lat", "lng", "note", "discount_code"):
        sess["ctx"].pop(k, None)


# ---------------------------------------------------------------- track, reorder

def _track(sess: dict) -> list[dict]:
    recent = db.customer_orders(sess["wa_id"], limit=1)
    oid = sess["ctx"].get("last_order_id") or (recent[0]["id"] if recent else None)
    o = db.get_order(oid) if oid else None
    if not o:
        return [text("No order on this number yet."), catalog_message()]
    msg = f"Order #{o['id']} is {STATUS_LABEL.get(o['status'], o['status'])}."
    if o.get("sr_tracking_url"):
        msg += f"\nRider: {o['sr_tracking_url']}"
    return [{"type": "cta", "text": msg, "label": "Track order",
             "url": f"{SITE}/track/{o.get('tracking_token') or o['id']}"}]


def _reorder(sess: dict) -> list[dict]:
    recent = db.customer_orders(sess["wa_id"], limit=1)
    o = db.get_order(recent[0]["id"]) if recent else None
    if not o:
        return [text("No previous order on this number yet."), catalog_message()]
    items = [{"id": it["item_id"], "qty": it["qty"]} for it in o["items"]]
    return _catalog_order(sess, items)


# ---------------------------------------------------------------- entry point

def handle(wa_id: str, incoming: str, profile_name: str | None = None,
           lat: str | None = None, lng: str | None = None,
           catalog_items: list[dict] | None = None,
           address: dict | None = None, flow_reply: dict | None = None) -> list[dict]:
    sess = sessions.load(wa_id)
    if profile_name and not sess["ctx"].get("profile_name"):
        sess["ctx"]["profile_name"] = profile_name
    if catalog_items is not None:
        out = _catalog_order(sess, catalog_items)
    elif flow_reply is not None:
        out = _on_checkout_form(sess, flow_reply)
    elif address is not None:
        out = _on_address_form(sess, address)
    elif lat and lng:
        out = _on_location(sess, lat, lng)
    else:
        out = _route(sess, incoming or "")
    sessions.save(sess)
    return [_menu_link(m, wa_id) if m.get("type") == "menu_link" else m for m in out]


def _on_address_form(sess: dict, values: dict) -> list[dict]:
    if sess["state"] != "address":
        return _welcome(sess)
    sess["ctx"]["address"] = _address_from_form(values)[:300]
    sess["ctx"]["pincode"] = (values.get("in_pin_code") or "").strip() or None
    if values.get("name"):
        sess["ctx"]["name"] = values["name"][:60]
    return _ask_location(sess)


def _on_location(sess: dict, lat: str, lng: str) -> list[dict]:
    if sess["state"] in ("location", "address"):
        if sess["state"] == "address" and not sess["ctx"].get("address"):
            sess["ctx"]["address"] = f"Pinned location ({float(lat):.5f}, {float(lng):.5f})"
        return _set_location(sess, lat, lng)
    return [text("Thanks! Send a cart from the menu first, then I'll ask where to deliver."),
            catalog_message()]


GREETINGS = {"HI", "HELLO", "HEY", "HII", "HIII", "HELO", "NAMASTE", "NAMASKAR", "VANAKKAM",
             "START", "ORDER", "MENU", "SHOW ME THE MENU", "BACK", "REQUEST_WELCOME",
             "GOOD MORNING", "GOOD AFTERNOON", "GOOD EVENING", "HI, I WANT TO PLACE AN ORDER."}
THANKS = {"THANKS", "THANK YOU", "THANKYOU", "TY", "OK", "OKAY", "OK THANKS", "OK THANK YOU",
          "GREAT", "NICE", "COOL", "👍", "🙏", "SUPER", "DONE", "RECEIVED", "GOT IT"}


def _route(sess: dict, incoming: str) -> list[dict]:
    raw = incoming.strip()
    t = raw.upper().rstrip("!.")
    if t.startswith("/"):  # WhatsApp / commands: /menu /track /reorder /kitchen
        t = {"/MENU": "MENU", "/TRACK": "TRACK", "/REORDER": "REORDER",
             "/KITCHEN": "KITCHEN"}.get(t.split()[0], t[1:])
    state = sess["state"]

    if t.startswith("FB:"):  # one-tap feedback button on the delivered message
        return _on_feedback_tap(sess, t)

    # Commands that work from anywhere
    if t in ("CANCEL", "CLEAR"):
        _reset_checkout(sess)
        return [text("Cancelled. Your cart is empty."), catalog_message("Order again any time:")]
    if t in ("TRACK", "STATUS", "TRACK MY ORDER"):
        return _track(sess)
    if t in ("REORDER", "REORDER MY LAST ORDER"):
        return _reorder(sess)
    if t == "PICKS":
        return [picks_message() or catalog_message()]
    if t == "HELP":
        return [text(HELP_TEXT), catalog_message()]
    if t == "CART":
        return _ask_order_type(sess) if sess["cart"] else [catalog_message("Your cart is empty.")]
    if t == "EDIT":
        sess["state"] = "root"
        return [catalog_message("Change your cart in the menu and send it again.")]
    if t.startswith("CAT_") and t[4:].isdigit():
        return category_products(int(t[4:]))
    if t in ("BROWSE", "SECTIONS"):
        return [category_list(
            "Pick a section to see its dishes, tap *Add* on what you like, then open the cart "
            "and *Send*.") or full_catalogue()]
    if t in ("FULL MENU", "CATALOGUE", "CATALOG", "ALL"):
        return [full_catalogue()]
    if t in ("KITCHEN", "TALK TO THE KITCHEN", "CALL", "CONTACT"):
        return _ask_kitchen(sess)
    if t in GREETINGS:
        if state in CHECKOUT_STATES and sess["cart"]:
            body, _ = cart_summary(sess)
            return [buttons(f"Welcome back! You were in the middle of an order:\n{body}",
                            [("CONTINUE", "Continue order"), ("RESTART", "Start over")])]
        return _welcome(sess)
    if t == "CHANGE" and sess["cart"]:
        return _ask_order_type(sess)
    if t == "RESTART":
        _reset_checkout(sess)
        return _welcome(sess)
    if t == "CONTINUE":
        return _resume(sess)
    if state == "ask_kitchen":
        return _relay(sess, raw)
    if state == "feedback" and len(raw) >= 2 and not raw.startswith("["):
        return _feedback_comment(sess, raw)
    if t in THANKS and state not in CHECKOUT_STATES:
        return [text("You're welcome! 🙏 Send *menu* any time to order again.")]

    if state == "checkout_flow":
        if t in ("DELIVERY", "PICKUP", "D", "P"):
            state = sess["state"] = "type"
        else:
            return _ask_order_type(sess)

    if state == "type":
        if t in ("DELIVERY", "D"):
            sess["ctx"]["order_type"] = "delivery"
            return _ask_address(sess)
        if t in ("PICKUP", "P"):
            sess["ctx"]["order_type"] = "pickup"
            sess["ctx"]["fee"] = 0
            return _ask_when(sess)
        return _ask_order_type(sess)

    if state == "address":
        if t == "SAVED" and sess["ctx"].get("saved_address"):
            sess["ctx"]["address"] = sess["ctx"]["saved_address"]
            return _ask_location(sess)
        if t == "NEWADDR":
            return _ask_address(sess)
        if len(raw) >= 10:  # typed address (non-Indian numbers, or a fallback)
            sess["ctx"]["address"] = raw[:300]
            return _ask_location(sess)
        return _ask_address(sess)

    if state == "location":
        return [text("Tap 📎 → Location → *Send your current location* (or pick the spot on the map)."),
                *_ask_location(sess)]

    if state == "when":
        out = _set_when(sess, t) if t.startswith("WHEN_") else None
        return out or _ask_when(sess)

    if state == "payment":
        if t in ("PAY_UPI", "UPI"):
            sess["ctx"]["payment"] = "UPI"
        elif t in ("PAY_COD", "CASH", "COD"):
            sess["ctx"]["payment"] = "COD"
        else:
            return [buttons("How would you like to pay?", [("PAY_UPI", "UPI"), ("PAY_COD", "Cash")])]
        return _summary(sess)

    if state == "confirm":
        if t in ("PLACE", "YES", "CONFIRM"):
            return _place_order(sess)
        code_out = _try_code(sess, raw)
        if code_out:
            return code_out
        if len(raw) >= 3:  # "less spicy please" -> a note for the kitchen
            note = (sess["ctx"].get("note", "") + " " + raw).strip()[:300]
            sess["ctx"]["note"] = note
            return [text(f"📝 Noted for the kitchen: “{note}”")] + _summary(sess)
        return _summary(sess)

    # A dish name ("paneer", "aloo paratha") is a search; anything else,
    # like a question, goes to the kitchen.
    if "?" not in raw and len(raw.split()) <= 5 and not raw.startswith("["):
        found = _search(raw)
        if found:
            return found
    if len(raw) >= 2 and not raw.startswith("["):
        return _relay(sess, raw)
    return _welcome(sess)


def _resume(sess: dict) -> list[dict]:
    """Pick checkout up where the customer left it."""
    state = sess["state"]
    if not sess["cart"]:
        return _welcome(sess)
    if state == "address":
        return _ask_address(sess)
    if state == "location":
        return _ask_location(sess)
    if state == "when":
        return _ask_when(sess)
    if state == "payment":
        return [buttons("How would you like to pay?", [("PAY_UPI", "UPI"), ("PAY_COD", "Cash")])]
    if state == "confirm":
        return _summary(sess)
    return _ask_order_type(sess)
