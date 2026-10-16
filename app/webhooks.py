"""WhatsApp Cloud API + Borzo delivery webhook endpoints.

GET  /webhook/whatsapp  — Meta's verification handshake.
POST /webhook/whatsapp  — inbound messages + status events.
POST /webhook/borzo     — Borzo delivery status updates.
"""
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

from .config import WHATSAPP_APP_SECRET, WHATSAPP_PHONE_ID, WHATSAPP_VERIFY_TOKEN, ADMIN_PHONE
from .business import business
from .whatsapp import client, conversation, sessions
from .petpooja.config import PETPOOJA_REST_ID, PETPOOJA_WEBHOOK_TOKEN

log = logging.getLogger("webhook")
router = APIRouter(prefix="/webhook", tags=["webhook"])


def _echo_customers(body: dict) -> list[str]:
    """Customer numbers whose chats the business answered from the WhatsApp
    Business app (Coexistence `smb_message_echoes` webhook). Once a human has
    replied to a chat, the bot stays out of it."""
    customers = set()
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for thread in value.get("smb_message_echoes", []):
                tid = thread.get("id")
                for msg in thread.get("messages", []):
                    cust = msg.get("to") or tid or msg.get("from")
                    if cust and cust != WHATSAPP_PHONE_ID:
                        customers.add(str(cust))
    return list(customers)


def _extract_messages(body: dict) -> list[dict]:
    out = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("from") == WHATSAPP_PHONE_ID:
                    continue  # echo of a message WE sent / the owner's app reply — never self-reply
                m = msg.get("text", {}).get("body")
                lat = None
                lng = None
                items = None
                address = None
                flow_reply = None
                media = None
                if msg.get("type") == "interactive":
                    intr = msg.get("interactive", {})
                    if intr.get("type") == "button_reply":
                        m = intr.get("button_reply", {}).get("id")
                    elif intr.get("type") == "list_reply":
                        m = intr.get("list_reply", {}).get("id")
                    elif intr.get("type") == "nfm_reply":
                        # Address form (address_message) or a Flow: the
                        # filled-in values come as a JSON string.
                        try:
                            resp = json.loads(intr.get("nfm_reply", {}).get("response_json") or "{}")
                        except ValueError:
                            resp = {}
                        if "flow_token" in resp and "values" not in resp:
                            flow_reply = resp  # checkout Flow submitted
                            m = "[Checkout form]"
                        else:
                            address = resp.get("values") or resp
                            m = "[Address form]"
                elif msg.get("type") == "request_welcome":
                    m = "REQUEST_WELCOME"  # first time the customer opens the chat
                elif msg.get("type") == "order":
                    # Cart sent from the WhatsApp catalogue: product ids are
                    # our menu ids (the feed at /whatsapp-catalog.csv).
                    items = [{"id": p.get("product_retailer_id"), "qty": int(p.get("quantity") or 1)}
                             for p in msg.get("order", {}).get("product_items", [])]
                    m = "[Catalogue order]"
                elif msg.get("type") == "location":
                    loc = msg.get("location", {})
                    lat = str(loc.get("latitude", ""))
                    lng = str(loc.get("longitude", ""))
                    m = f"[Location: {lat}, {lng}]"
                elif msg.get("type") in ("image", "audio", "video", "document", "sticker"):
                    media = {"type": msg["type"], "id": msg.get(msg["type"], {}).get("id"),
                             "caption": msg.get(msg["type"], {}).get("caption")}
                    m = f"[{msg['type']}]"
                elif msg.get("type") == "button":  # quick-reply button on a template
                    m = msg.get("button", {}).get("payload") or msg.get("button", {}).get("text")
                if m is None:
                    continue  # reactions, unsupported types: nothing to answer
                profile_name = ""
                for contact in value.get("contacts", []):
                    profile_name = contact.get("profile", {}).get("name", "")
                out.append({
                    "wa_id": msg.get("from", ""),
                    "text": str(m),
                    "profile_name": profile_name,
                    "lat": lat,
                    "lng": lng,
                    "catalog_items": items,
                    "address": address,
                    "flow_reply": flow_reply,
                    "media": media,
                    "id": msg.get("id"),
                    "context": (msg.get("context") or {}).get("id"),
                })
    return out


_SEEN: dict[str, None] = {}


def _seen(message_id: str | None) -> bool:
    """True if this inbound message id was already handled (Meta redelivers
    webhooks it thinks failed)."""
    if not message_id:
        return False
    if message_id in _SEEN:
        return True
    _SEEN[message_id] = None
    if len(_SEEN) > 2000:
        for k in list(_SEEN)[:1000]:
            _SEEN.pop(k, None)
    return False


def _log_failed_statuses(body: dict) -> None:
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []):
                if st.get("status") == "failed":
                    log.warning("WhatsApp message to %s failed: %s", st.get("recipient_id"),
                                [(e.get("code"), e.get("title"), (e.get("error_data") or {}).get("details"))
                                 for e in st.get("errors", [])])


def _handle_admin(msg: dict) -> None:
    """Messages from the kitchen number (the owner): swipe-replies to alerts, Porter
    links and fares, menu commands. Never treated as a customer order."""
    from .kitchen_alerts import (handle_admin_reply, handle_admin_tracking_link,
                                 looks_like_tracking_link)
    from .whatsapp.admin_commands import handle_admin_command, is_admin_command
    text = msg["text"] or ""
    try:
        reply = handle_admin_reply(text, msg.get("context")) if msg.get("context") else None
        if reply is None and looks_like_tracking_link(text):
            reply = handle_admin_tracking_link(text)
        if reply is None and is_admin_command(text):
            reply = handle_admin_command(text)
        if reply is None:
            reply = handle_admin_command("help")
    except Exception:
        log.exception("admin message failed: %s", text[:200])
        reply = "Something went wrong saving that. Please try again, or tell the admin."
    try:
        client.send_text(msg["wa_id"], reply)
    except Exception:
        log.exception("reply to admin failed")
    log.info("admin %r -> %r", text[:80], reply[:120])


def _customer_media(msg: dict) -> list[dict]:
    """Photos (often UPI screenshots), voice notes and files go to the kitchen."""
    from .kitchen_alerts import forward_media
    media = msg["media"]
    if media["type"] == "sticker" or not media.get("id"):
        return []
    forward_media(msg["wa_id"], msg["profile_name"], media["type"], media["id"], media.get("caption"))
    if media["type"] == "audio":
        return [{"type": "text", "text": "Thanks! I've passed your voice note to the kitchen. "
                 f"If it's urgent, please call {business.phone_local}."}]
    return [{"type": "text", "text": "Thanks! I've sent this to the kitchen, they'll get back to you here."}]


def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    if not WHATSAPP_APP_SECRET:
        return True
    if not signature:
        return False
    expected = hmac.new(WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + expected, signature)


@router.get("/whatsapp")
def verify(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("Verification failed", status_code=403)


@router.get("/catalog")
def verify_catalog(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    """Meta's handshake for the Catalog webhook (App Dashboard → Webhooks →
    Catalog). Same verify token as the WhatsApp webhook."""
    return verify(hub_mode, hub_token, hub_challenge)


@router.post("/catalog")
async def catalog_event(request: Request, x_hub_signature_256: str | None = Header(None)):
    """product_feed / items_batch results for the Menu catalogue, logged so
    a broken feed run (dishes missing or wrong in WhatsApp) shows in Railway."""
    raw = await request.body()
    if not _verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            log.info("catalog webhook %s: %s", change.get("field"), json.dumps(value)[:1000])
    return {"status": "ok"}


@router.post("/whatsapp")
async def inbound(request: Request, x_hub_signature_256: str | None = Header(None)):
    raw = await request.body()
    if not _verify_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    log.debug("webhook payload: %s", body)
    for cust in _echo_customers(body):
        sessions.set_human(cust, True)
        log.info("human took over chat for %s", cust)
    _log_failed_statuses(body)
    for msg in _extract_messages(body):
        if _seen(msg.get("id")):
            continue  # Meta retries deliveries; never answer the same message twice
        if ADMIN_PHONE and msg["wa_id"] == ADMIN_PHONE:
            _handle_admin(msg)
            continue
        if sessions.is_human(msg["wa_id"]):
            log.info("bot paused for %s (human-owned)", msg["wa_id"])
            continue
        try:
            if msg.get("media"):
                replies = _customer_media(msg)
            else:
                replies = conversation.handle(msg["wa_id"], msg["text"], msg["profile_name"],
                                              lat=msg.get("lat"), lng=msg.get("lng"),
                                              catalog_items=msg.get("catalog_items"),
                                              address=msg.get("address"),
                                              flow_reply=msg.get("flow_reply"))
            for reply in replies:
                if reply.get("type") == "relay":
                    from .kitchen_alerts import relay_question
                    relay_question(msg["wa_id"], msg["profile_name"], reply["text"])
                    continue
                try:
                    client.send_outbound(msg["wa_id"], reply)
                except Exception:
                    # One rejected message must not silence the rest.
                    log.exception("reply to %s failed: %s", msg["wa_id"], reply.get("type"))
        except Exception:
            log.exception("failed to process message from %s", msg["wa_id"])
    return {"status": "ok"}


# ---- Borzo delivery status webhook ----

@router.post("/borzo")
async def borzo_webhook(request: Request, x_dv_signature: str | None = Header(None)):
    """Borzo sends status updates when orders/deliveries change."""
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Verify signature if callback token is set
    from .delivery.config import BORZO_CALLBACK_TOKEN
    if BORZO_CALLBACK_TOKEN and x_dv_signature:
        expected = hmac.new(BORZO_CALLBACK_TOKEN.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, x_dv_signature):
            log.warning("Borzo webhook: invalid signature")
            raise HTTPException(status_code=403, detail="Invalid signature")

    event_type = body.get("event_type", "")
    log.info("Borzo webhook: %s", event_type)

    # Handle delivery status changes
    if event_type in ("delivery_changed", "delivery_created"):
        delivery = body.get("delivery", {})
        status = delivery.get("status", "")
        order_id = delivery.get("client_order_id")  # our internal order ID
        if order_id:
            _handle_borzo_delivery_status(int(order_id), status, delivery)

    # Handle order status changes
    elif event_type in ("order_changed", "order_created"):
        order = body.get("order", {})
        status = order.get("status", "")
        # Find our order ID from the points
        for point in order.get("points", []):
            cid = point.get("client_order_id")
            if cid:
                _handle_borzo_order_status(int(cid), status, order)
                break

    return {"status": "ok"}


# Borzo delivery status → our order status
_BORZO_DELIVERY_STATUS_MAP = {
    "courier_assigned": "out_for_delivery",
    "courier_departed": "out_for_delivery",
    "courier_at_pickup": "out_for_delivery",
    "parcel_picked_up": "out_for_delivery",
    "courier_arrived": "out_for_delivery",
    "finished": "delivered",
    "canceled": "cancelled",
    "return_planned": "cancelled",
    "return_finished": "cancelled",
}

_BORZO_ORDER_STATUS_MAP = {
    "new": "ready",
    "available": "ready",
    "active": "out_for_delivery",
    "completed": "delivered",
    "canceled": "cancelled",
}


def _handle_borzo_delivery_status(order_id: int, status: str, delivery: dict) -> None:
    """Update order status based on Borzo delivery webhook."""
    from . import db
    mapped = _BORZO_DELIVERY_STATUS_MAP.get(status)
    if not mapped:
        log.info("Borzo delivery status '%s' for order %s — no action", status, order_id)
        return
    o = db.get_order(order_id)
    if not o:
        log.warning("Borzo webhook: order %s not found", order_id)
        return
    # Only advance, never regress
    current = o["status"]
    if current == "delivered" or current == "cancelled":
        return
    db.update_order_status(order_id, mapped)
    log.info("Order %s: %s → %s (Borzo delivery: %s)", order_id, current, mapped, status)
    _send_status_whatsapp_if_needed(o, mapped)
    _relay_rider_status_to_petpooja(order_id, status)


def _handle_borzo_order_status(order_id: int, status: str, order: dict) -> None:
    """Update order status based on Borzo order webhook."""
    from . import db
    mapped = _BORZO_ORDER_STATUS_MAP.get(status)
    if not mapped:
        return
    o = db.get_order(order_id)
    if not o:
        return
    current = o["status"]
    if current == "delivered" or current == "cancelled":
        return
    db.update_order_status(order_id, mapped)
    log.info("Order %s: %s → %s (Borzo order: %s)", order_id, current, mapped, status)
    _send_status_whatsapp_if_needed(o, mapped)


def _send_status_whatsapp_if_needed(order: dict, status: str) -> None:
    """Send notification on delivery status change (non-fatal).

    Only delivered/cancelled move the customer to act/settle up; intermediate
    webhook statuses are handled by the dispatch flow, so skip them here.
    """
    if status not in ("delivered", "cancelled"):
        return
    from .notify import notify_status
    notify_status(order, status)


# ---- Borzo -> Petpooja rider status relay ----
# Piggybacks on the existing Borzo webhook: whenever our courier's status
# changes and this order was pushed to Petpooja POS, tell Petpooja too (their
# "Rider Information" webhook), so the kitchen terminal shows live courier
# status instead of just "dispatched". Best-effort — never blocks the primary
# Borzo status handling above.
_BORZO_TO_PETPOOJA_RIDER_STATUS = {
    "courier_assigned": "rider-assigned",
    "courier_arrived": "rider-arrived",
    "courier_at_pickup": "rider-arrived",
    "parcel_picked_up": "pickedup",
    "finished": "delivered",
}


def _relay_rider_status_to_petpooja(order_id: int, borzo_status: str) -> None:
    mapped = _BORZO_TO_PETPOOJA_RIDER_STATUS.get(borzo_status)
    if not mapped:
        return
    try:
        from . import db
        from .petpooja.client import is_configured, push_rider_status
        if not is_configured():
            return
        o = db.get_order(order_id)
        if not o or not o.get("petpooja_order_id"):
            return
        push_rider_status(order_id, mapped)
    except Exception:
        log.exception("Petpooja rider status relay failed for order %s", order_id)


# ---- Petpooja POS webhooks ----
# Endpoints Petpooja calls (we host these, per the "Push Menu" / "Order
# Callback" / stock+store status API docs). Petpooja's docs don't specify a
# request-signing scheme for these, so we hand Petpooja these URLs with our
# own shared-secret token baked in as ?t=... — verified below.

def _check_petpooja_token(t: str | None) -> None:
    if PETPOOJA_WEBHOOK_TOKEN and t != PETPOOJA_WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


@router.post("/petpooja/order-callback")
async def petpooja_order_callback(request: Request, t: str | None = Query(None)):
    """Petpooja POS -> us: order status changed (accepted/dispatch/ready/delivered/cancelled).

    Matches the docs' 200 OK response exactly — an empty body (their
    backend tracks callbacks by HTTP status code only, sample response shows
    content-length: 0). All per-request outcomes are logged; malformed
    requests get a 400, bad token gets a 403.
    """
    _check_petpooja_token(t)
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    from . import db
    from .petpooja.mapping import petpooja_status_to_order_status

    order_id_raw = body.get("orderID")
    client_order_id_raw = body.get("clientOrderID") or body.get("client_order_id")
    status_code = body.get("status")
    if (not order_id_raw and not client_order_id_raw) or status_code is None:
        raise HTTPException(status_code=400, detail="Missing orderID/clientOrderID or status")

    mapped = petpooja_status_to_order_status(status_code)
    if not mapped:
        log.info("Petpooja callback: unrecognised status %s for order %s", status_code, order_id_raw or client_order_id_raw)
        return Response(status_code=200, media_type="application/json")

    from . import db
    # Prefer the id Petpooja echoes back that is *ours* (clientOrderID is
    # documented as == our own order id) — primary key match, no ambiguity.
    # Fall back to their orderID: try it as our numeric id too, then the
    # stored petpooja_order_id column. Accepting both costs nothing and keeps
    # callbacks working whichever field production sends.
    o = None
    if client_order_id_raw:
        try:
            o = db.get_order(int(client_order_id_raw))
        except (TypeError, ValueError):
            pass
    if o is None and order_id_raw:
        try:
            o = db.get_order(int(order_id_raw))
        except (TypeError, ValueError):
            pass
        if o is None:
            o = db.get_order_by_petpooja(str(order_id_raw))
    if not o:
        log.info("Petpooja callback: order %s not found", order_id_raw or client_order_id_raw)
        return Response(status_code=200, media_type="application/json")
    if o["status"] in ("delivered", "cancelled"):
        log.info("Petpooja callback: no-op for order %s (already closed)", order_id_raw or client_order_id_raw)
        return Response(status_code=200, media_type="application/json")

    order_id = o["id"]
    previous = o["status"]
    db.update_order_status(order_id, mapped)
    log.info("Order %s: %s -> %s (Petpooja callback: %s)", order_id, o["status"], mapped, status_code)
    _send_status_whatsapp_if_needed(db.get_order(order_id), mapped)
    # Rider booking: the POS "Food Ready" tap books the courier automatically
    # for delivery orders (guarded — see _maybe_auto_dispatch).
    if mapped == "preparing" and previous == "new":
        # POS Accept: get the rider booking started while the food cooks.
        _maybe_auto_dispatch(order_id, "accepted, book a 2-wheeler now (food ready in about 20 min)")
    elif mapped == "ready":
        _maybe_auto_dispatch(order_id, "food is READY and no rider is booked yet")
    return Response(status_code=200, media_type="application/json")


def _maybe_auto_dispatch(order_id: int, reason: str = "book a 2-wheeler now") -> None:
    """Rider booking, driven by the POS (Accept, then Food Ready).

    Porter API configured -> book it and notify the customer. Otherwise (the
    normal case today) send the owner the Porter booking card (WhatsApp, plus
    Telegram if set up, see app/kitchen_alerts.py); she books in the Porter
    app and forwards the tracking link back to the business WhatsApp.
    Skipped when the order isn't a delivery or a rider is already recorded.
    Best-effort — never blocks the callback's 200."""
    from . import db
    o = db.get_order(order_id)
    if not o or o["order_type"] != "delivery":
        return
    if o.get("sr_tracking_url") or o.get("sr_courier"):
        return
    if not o.get("delivery_address"):
        log.info("Petpooja callback: order %s food-ready but missing address — holding dispatch", order_id)
        return
    if o.get("address_flagged"):
        # Still tell the owner (a silent skip meant nobody booked a rider), but
        # never auto-book: she checks the address with the customer first.
        from .kitchen_alerts import porter_card
        porter_card(o, reason)
        return
    try:
        from .orders import dispatch_rider
        result = dispatch_rider(order_id)
    except Exception:
        log.exception("Rider booking failed for order %s (Petpooja food-ready); use the admin Book button", order_id)
        return
    if result.get("manual"):
        from .kitchen_alerts import porter_card
        sent = porter_card(db.get_order(order_id), reason)
        log.info("Order %s: Porter booking card -> %s", order_id, ", ".join(sent) or "NO CHANNEL (admin panel only)")
        return
    log.info("Order %s booked on Porter API on food-ready", order_id)
    from .notify import notify_dispatch
    try:
        notify_dispatch(db.get_order(order_id), result)
    except Exception:
        log.exception("Dispatch notification failed for order %s", order_id)


@router.post("/petpooja/menu")
async def petpooja_push_menu(request: Request, t: str | None = Query(None)):
    """Petpooja POS -> us: menu changed. Cached only for now — NOT wired into
    the live customer-facing menu (data/menu.json stays the source until the
    item-id reconciliation described in app/petpooja/mapping.py is done and
    someone deliberately flips the switch)."""
    _check_petpooja_token(t)
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    from .config import DATA_DIR
    cache_file = DATA_DIR / "petpooja_menu_raw.json"
    try:
        cache_file.write_text(json.dumps(body, indent=2), encoding="utf-8")
        log.info("Petpooja push-menu received and cached (%d bytes)", len(json.dumps(body)))
    except Exception:
        log.exception("Failed to cache Petpooja pushed menu")

    return {"success": "1", "message": "Menu items are successfully listed."}


@router.post("/petpooja/stock")
async def petpooja_update_stock(request: Request, t: str | None = Query(None)):
    """Petpooja POS -> us: item/addon marked in/out of stock. Logged only for
    now — see the item-id note in app/petpooja/mapping.py for why this isn't
    wired into app.db.set_availability() yet."""
    _check_petpooja_token(t)
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    log.info("Petpooja stock toggle: %s", body)
    return {"code": 200, "status": "success", "message": "Stock status updated successfully"}


@router.api_route("/petpooja/store-status", methods=["GET", "POST"])
async def petpooja_get_store_status(t: str | None = Query(None)):
    """Petpooja POS -> us: 'is the store open for online orders'."""
    _check_petpooja_token(t)
    from . import db
    status = db.get_store_status()
    return {
        "http_code": 200,
        "status": "success",
        "store_status": "1" if status["is_open"] else "0",
        "message": "Store Delivery Status fetched successfully",
    }


@router.post("/petpooja/store-status/update")
async def petpooja_update_store_status(request: Request, t: str | None = Query(None)):
    """Petpooja POS -> us: merchant flipped store open/closed on the POS terminal.
    Gates new order creation — see the store-status check in app/orders.py."""
    _check_petpooja_token(t)
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    from . import db
    is_open = str(body.get("store_status")) == "1"
    db.set_store_status(is_open, reason=body.get("reason"), turn_on_time=body.get("turn_on_time"))
    log.info("Store status set to %s (reason: %s)", "open" if is_open else "closed", body.get("reason"))
    return {
        "http_code": 200,
        "status": "success",
        "message": f"Store Status updated successfully for store {body.get('restID', PETPOOJA_REST_ID)}",
    }
