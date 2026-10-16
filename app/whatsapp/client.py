"""WhatsApp Cloud API client.

Without credentials it runs in dry-run mode: replies are appended to
WHATSAPP_DRY_LOG so the bot can be developed/tested locally.
"""
from ..business import business
import json
import logging
from pathlib import Path

import httpx

from ..config import (
    WHATSAPP_DRY_LOG,
    WHATSAPP_GRAPH_URL,
    WHATSAPP_GRAPH_VERSION,
    WHATSAPP_PHONE_ID,
    WHATSAPP_TOKEN,
)

log = logging.getLogger("whatsapp")


def wa_number(to: str) -> str:
    """Cloud API wants the full international number without '+'. Customer
    phones are stored as bare 10-digit Indian numbers, which Meta reads as a
    different country (or rejects), so add 91 to anything 10 digits long."""
    digits = "".join(ch for ch in str(to or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return "91" + digits if len(digits) == 10 else digits


def configured() -> bool:
    return bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID)


def _url() -> str:
    return f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}/{WHATSAPP_PHONE_ID}/messages"


def _dry_log(payload: dict) -> None:
    try:
        path = Path(WHATSAPP_DRY_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("dry-run log write failed: %s", e)
    log.info("DRY-RUN WhatsApp message: %s", payload.get("text") or payload.get("interactive"))


def _send(to: str, kind: str, body: dict, preview: str = "") -> dict:
    """POST one message; dry-run log without credentials. Raises
    WhatsAppError carrying Meta's reason (e.g. 131047 = outside the 24 h
    window, 131037 = display name not approved yet)."""
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
               "to": wa_number(to), "type": kind, kind: body}
    if not configured():
        _dry_log(payload)
        return {"dry_run": True, "to": to, "text": preview}
    return _post(payload)


def _btn(b) -> dict:
    bid, title = b if isinstance(b, (list, tuple)) else (b, b)
    return {"type": "reply", "reply": {"id": str(bid)[:256], "title": str(title)[:20]}}


def send_text(to: str, body: str) -> dict:
    return _send(to, "text", {"body": body[:4096], "preview_url": True}, body)


def send_buttons(to: str, body: str, buttons: list) -> dict:
    return _send(to, "interactive", {
        "type": "button", "body": {"text": body[:1024]},
        "action": {"buttons": [_btn(b) for b in buttons[:3]]}}, body)


def send_list(to: str, body: str, button: str, sections: list[dict]) -> dict:
    for sec in sections:
        sec["title"] = sec.get("title", "")[:24]
        for row in sec.get("rows", []):
            row["title"] = row["title"][:24]
            if "description" in row:
                row["description"] = row["description"][:72]
    return _send(to, "interactive", {
        "type": "list", "body": {"text": body[:4096]},
        "action": {"button": button[:20], "sections": sections}}, body)


def send_catalog(to: str, body: str) -> dict:
    """The whole menu in WhatsApp's own catalogue view."""
    return _send(to, "interactive", {
        "type": "catalog_message", "body": {"text": body[:1024]},
        "action": {"name": "catalog_message"}}, body)


def send_products(to: str, header: str, body: str, sections: list[dict]) -> dict:
    """Up to 30 dishes from the catalogue, in up to 10 titled sections."""
    from ..config import WHATSAPP_CATALOG_ID
    return _send(to, "interactive", {
        "type": "product_list", "header": {"type": "text", "text": header[:60]},
        "body": {"text": body[:1024]},
        "action": {"catalog_id": WHATSAPP_CATALOG_ID, "sections": [
            {"title": sec["title"][:24],
             "product_items": [{"product_retailer_id": pid} for pid in sec["ids"]]}
            for sec in sections]}}, body)


def send_location_request(to: str, body: str) -> dict:
    return _send(to, "interactive", {
        "type": "location_request_message", "body": {"text": body[:1024]},
        "action": {"name": "send_location"}}, body)


def send_address_request(to: str, body: str, values: dict | None = None) -> dict:
    """India-only native address form; the reply arrives as an nfm_reply."""
    params = {"country": "IN"}
    if values:
        params["values"] = {k: v for k, v in values.items() if v}
    return _send(to, "interactive", {
        "type": "address_message", "body": {"text": body[:1024]},
        "action": {"name": "address_message", "parameters": params}}, body)


def send_cta(to: str, body: str, label: str, url: str) -> dict:
    return _send(to, "interactive", {
        "type": "cta_url", "body": {"text": body[:1024]},
        "action": {"name": "cta_url", "parameters": {"display_text": label[:20], "url": url}}}, body)


def send_flow(to: str, flow_id: str, header: str, body: str, cta: str, screen: str,
              data: dict, token: str, mode: str = "") -> dict:
    """Open a WhatsApp Flow (a native form) on `screen` with initial `data`."""
    params = {"flow_message_version": "3", "flow_token": token, "flow_id": flow_id,
              "flow_cta": cta[:20], "flow_action": "navigate",
              "flow_action_payload": {"screen": screen, "data": data}}
    if mode:
        params["mode"] = mode
    return _send(to, "interactive", {
        "type": "flow", "header": {"type": "text", "text": header[:60]},
        "body": {"text": body[:1024]}, "footer": {"text": business.name},
        "action": {"name": "flow", "parameters": params}}, body)


def send_outbound(to: str, msg: dict) -> dict:
    kind = msg.get("type")
    if kind == "flow":
        try:
            return send_flow(to, msg["flow_id"], msg["header"], msg["text"], msg["cta"],
                             msg["screen"], msg["data"], msg["token"], msg.get("mode", ""))
        except WhatsAppError as e:
            log.warning("flow message failed (%s); using the step-by-step fallback", e)
            return send_outbound(to, msg["fallback"])
    if kind == "buttons":
        return send_buttons(to, msg["text"], msg["buttons"])
    if kind == "list":
        return send_list(to, msg["text"], msg["button"], msg["sections"])
    if kind in ("catalog", "products"):
        try:
            if kind == "catalog":
                return send_catalog(to, msg["text"])
            return send_products(to, msg["header"], msg["text"], msg["sections"])
        except WhatsAppError as e:
            # Catalogue not usable yet (e.g. a new catalogue still being
            # indexed: "Products not found in FB catalog"): the website menu
            # is the same food with the same cart and checkout.
            log.warning("catalogue message failed (%s); sending the menu link", e)
            return send_cta(to, "Here's today's menu. Tap below, add dishes to the cart and "
                            "check out, it takes a minute.", "View menu", f"{business.site_url}/menu")
    if kind == "location_request":
        return send_location_request(to, msg["text"])
    if kind == "address":
        try:
            return send_address_request(to, msg["text"], msg.get("values"))
        except WhatsAppError as e:  # not enabled for this number: ask in text
            log.warning("address form failed (%s); asking in text", e)
            return send_text(to, "Please type your full delivery address: house/flat, street, area, landmark, pincode.")
    if kind == "cta":
        return send_cta(to, msg["text"], msg["label"], msg["url"])
    if kind == "template":
        return send_template(to, msg["name"], msg.get("language", "en"), msg.get("params", []))
    return send_text(to, msg["text"])


class WhatsAppError(Exception):
    """A Graph API refusal, with Meta's error code (e.g. 132001 = template
    not found in that language, 132000 = wrong number of parameters)."""

    def __init__(self, status: int, code, message: str):
        self.status, self.code = status, code
        super().__init__(f"WhatsApp {status} (code {code}): {message}")


# Library templates added in WhatsApp Manager default to "English (US)" =
# en_US, which Meta treats as a different template from "en". Try the asked
# language first, then the other English variants, and remember what worked.
_ENGLISH = ("en_US", "en", "en_GB")
_lang_that_worked: dict[str, str] = {}


def _post(payload: dict) -> dict:
    with httpx.Client(timeout=20) as c:
        r = c.post(_url(), headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, json=payload)
    if r.is_success:
        return r.json()
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    raise WhatsAppError(r.status_code, err.get("code"), err.get("message") or r.text[:200])


def send_template(to: str, name: str, language: str = "en",
                  params: list[dict] | None = None,
                  button_url_suffix: str | None = None,
                  quick_reply_payloads: list[str] | None = None) -> dict:
    """Send a pre-approved template message.

    Each param is {"type": "text", "text": "value"}. `button_url_suffix`
    fills a dynamic URL button ({{1}} at the end of the button's URL).
    """
    components = []
    if params:
        components.append({"type": "body", "parameters": params})
    if button_url_suffix:
        components.append({"type": "button", "sub_type": "url", "index": "0",
                           "parameters": [{"type": "text", "text": button_url_suffix}]})
    for i, payload in enumerate(quick_reply_payloads or []):
        components.append({"type": "button", "sub_type": "quick_reply", "index": str(i),
                           "parameters": [{"type": "payload", "payload": payload}]})
    langs = [_lang_that_worked.get(name, language)]
    langs += [l for l in (language, *_ENGLISH) if l not in langs]
    last_err = None
    for lang in langs:
        template: dict = {"name": name, "language": {"code": lang}}
        if components:
            template["components"] = components
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": wa_number(to),
            "type": "template",
            "template": template,
        }
        if not configured():
            _dry_log(payload)
            return {"dry_run": True, "to": to, "template": name}
        try:
            result = _post(payload)
            _lang_that_worked[name] = lang
            return result
        except WhatsAppError as e:
            last_err = e
            if e.code != 132001:  # only "not found in this language" is worth retrying
                break
    log.warning("template %s failed: %s", name, last_err)
    raise last_err


def list_templates(waba_id: str) -> list[dict]:
    """Every message template on the WhatsApp Business Account, as Meta has
    them (name, status, language, category, components)."""
    url = f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}/{waba_id}/message_templates"
    out, params = [], {"fields": "name,status,language,category,components", "limit": 200}
    with httpx.Client(timeout=20) as c:
        while url:
            r = c.get(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, params=params)
            r.raise_for_status()
            data = r.json()
            out += data.get("data", [])
            url, params = data.get("paging", {}).get("next"), None
    return out


_waba_cache: list[str] = []


def spend(days: int = 30) -> dict:
    """What Meta has charged for messages over the last `days` days, from its
    pricing analytics: {"currency", "total", "rows": [{category, type, country, volume, cost}]}."""
    import json as _json
    import time as _time
    waba = waba_id()
    end = int(_time.time())
    start = end - days * 86400
    dims = _json.dumps(["PRICING_CATEGORY", "PRICING_TYPE", "COUNTRY"])
    field = f"pricing_analytics.start({start}).end({end}).granularity(DAILY).dimensions({dims})"
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}/{waba}",
                  headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
                  params={"fields": f"currency,{field}"})
        r.raise_for_status()
        data = r.json()
    rows: dict = {}
    for block in data.get("pricing_analytics", {}).get("data", []):
        for p in block.get("data_points", []):
            k = (p.get("pricing_category"), p.get("pricing_type"), p.get("country"))
            row = rows.setdefault(k, {"category": k[0], "type": k[1], "country": k[2], "volume": 0, "cost": 0.0})
            row["volume"] += p.get("volume") or 0
            row["cost"] += p.get("cost") or 0
    out = sorted(rows.values(), key=lambda r: -r["cost"])
    return {"currency": data.get("currency", "INR"), "days": days,
            "total": round(sum(r["cost"] for r in out), 2), "rows": out}


def waba_id() -> str:
    """The WhatsApp Business Account that owns WHATSAPP_PHONE_ID:
    WHATSAPP_WABA_ID if set, else looked up among the business portfolio's
    WABAs (a system-user token's debug_token carries no WABA id), so
    switching to another phone number needs no extra variable."""
    from ..config import META_BUSINESS_ID, WHATSAPP_WABA_ID
    if WHATSAPP_WABA_ID:
        return WHATSAPP_WABA_ID
    if _waba_cache:
        return _waba_cache[0]
    if not (WHATSAPP_TOKEN and WHATSAPP_PHONE_ID and META_BUSINESS_ID):
        return ""
    base = f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}"
    auth = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    with httpx.Client(timeout=20) as c:
        for edge in ("owned_whatsapp_business_accounts", "client_whatsapp_business_accounts"):
            r = c.get(f"{base}/{META_BUSINESS_ID}/{edge}", headers=auth, params={"fields": "id"})
            if not r.is_success:
                continue
            for waba in r.json().get("data", []):
                p = c.get(f"{base}/{waba['id']}/phone_numbers", headers=auth, params={"fields": "id"})
                if p.is_success and any(n.get("id") == WHATSAPP_PHONE_ID for n in p.json().get("data", [])):
                    _waba_cache.append(str(waba["id"]))
                    return _waba_cache[0]
    return ""


def create_template(waba: str, payload: dict) -> dict:
    """Submit a message template for Meta's review (UTILITY ones are usually
    approved within minutes)."""
    url = f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}/{waba}/message_templates"
    with httpx.Client(timeout=20) as c:
        r = c.post(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, json=payload)
    if r.is_success:
        return r.json()
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    raise WhatsAppError(r.status_code, err.get("code"),
                        err.get("error_user_msg") or err.get("message") or r.text[:200])


def edit_template(template_id: str, comps: list[dict]) -> dict:
    """Change an existing template's wording (Meta re-reviews it; an approved
    template can be edited once a day, ten times a month)."""
    url = f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}/{template_id}"
    with httpx.Client(timeout=20) as c:
        r = c.post(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
                   json={"components": comps})
    if r.is_success:
        return r.json()
    err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("error", {})
    raise WhatsAppError(r.status_code, err.get("code"),
                        err.get("error_user_msg") or err.get("message") or r.text[:200])


def set_conversational_automation(prompts: list[str], commands: list[dict]) -> dict:
    """Welcome-message event, ice breakers and / commands for the number."""
    url = f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}/{WHATSAPP_PHONE_ID}/conversational_automation"
    with httpx.Client(timeout=20) as c:
        r = c.post(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
                   json={"enable_welcome_message": True, "prompts": prompts, "commands": commands})
    if r.is_success:
        return r.json()
    raise WhatsAppError(r.status_code, None, r.text[:200])


def message_id(result: dict) -> str | None:
    """The wamid of a sent message (used to match the owner's swipe-replies)."""
    try:
        return (result or {}).get("messages", [{}])[0].get("id")
    except (AttributeError, IndexError):
        return None


def reupload_media(media_id: str) -> str:
    """Download a customer's media (by its inbound id) and upload it again so
    it can be sent on; returns the new media id."""
    base = f"{WHATSAPP_GRAPH_URL}/{WHATSAPP_GRAPH_VERSION}"
    auth = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    with httpx.Client(timeout=30) as c:
        meta = c.get(f"{base}/{media_id}", headers=auth)
        meta.raise_for_status()
        info = meta.json()
        blob = c.get(info["url"], headers=auth)
        blob.raise_for_status()
        mime = info.get("mime_type") or "application/octet-stream"
        up = c.post(f"{base}/{WHATSAPP_PHONE_ID}/media", headers=auth,
                    data={"messaging_product": "whatsapp", "type": mime},
                    files={"file": ("upload", blob.content, mime)})
        up.raise_for_status()
        return up.json()["id"]


def send_media(to: str, media_type: str, media_id: str, caption: str | None = None) -> dict:
    body = {"id": media_id}
    if caption and media_type in ("image", "video", "document"):
        body["caption"] = caption[:1024]
    return _send(to, media_type, body, caption or media_type)
