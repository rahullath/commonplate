"""Twilio SMS client — fallback channel when WhatsApp isn't verified yet.

Uses httpx (consistent with the rest of the app, no Twilio SDK dependency).
Auth is via an API Key SID + secret scoped to an Account SID.

Trial accounts (TWILIO_TRIAL=1, the default) can't send meaningful custom SMS
(custom bodies rejected with 572006; only pre-defined ContentSid templates reach
verified recipients), so on trial send_status() logs and skips. After upgrading
to a paid account (with an Indian DLT sender ID), set TWILIO_TRIAL="" and
free-form order-status SMS is sent. See docs/META_CONTINGENCY_PLAN.md §7.
"""
import logging

import httpx

from ..config import (
    TWILIO_ACCOUNT_SID,
    TWILIO_API_KEY,
    TWILIO_API_SECRET,
    TWILIO_API_URL,
    TWILIO_FROM,
    TWILIO_TRIAL,
)

log = logging.getLogger("sms")


def configured() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_API_KEY and TWILIO_API_SECRET and TWILIO_FROM)


def _send(to: str, body: str) -> dict:
    """Low-level POST to the Messages resource. Raises on Twilio error codes."""
    url = f"{TWILIO_API_URL}/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {
        "To": to,
        "From": TWILIO_FROM,
        "Body": body,
    }
    with httpx.Client(timeout=20) as c:
        r = c.post(url, data=data, auth=(TWILIO_API_KEY, TWILIO_API_SECRET))
        payload = r.json()
    if r.status_code >= 400 or payload.get("error_code") or payload.get("status") == "failed":
        raise RuntimeError(f"Twilio send failed: {payload.get('code')} {payload.get('message')}")
    return payload


# Twilio-predefined template names available on trial (verified live). Kept for
# reference only — NOT used for real order status, because trial delivers only
# these generic Twilio texts and only to verified numbers, which is meaningless
# gibberish for customers. Real SMS needs TWILIO_TRIAL="" + a paid account with
# an Indian DLT sender ID.
_TRIAL_TEMPLATES = {
    "order": "sms_order_confirmation",
    "delivery": "sms_delivery_updates",
}


def send_status(to: str, message: str, status: str = "") -> dict:
    """Send an order-status SMS.

    Trial accounts can't send real order-status SMS: custom bodies are rejected
    (572006) and only Twilio-predefined template texts reach verified numbers —
    which is meaningless for customers. So on trial this logs and skips rather
    than spamming generic texts. Flip TWILIO_TRIAL="" on a paid account (with an
    Indian DLT sender ID) to send the real `message`.
    """
    if not configured():
        log.info("Twilio not configured — skipping SMS to %s", to)
        return {"skipped": True, "to": to}
    if TWILIO_TRIAL:
        # See docstring / docs/META_CONTINGENCY_PLAN.md §7: not usable for real
        # customer notifications on trial. Skip silently to avoid random texts.
        log.info("Twilio trial — real SMS unsupported (572006); skipping to %s", to)
        return {"skipped": True, "to": to, "trial": True}
    payload = _send(to, message)
    log.info("Twilio SMS sent to %s: sid=%s status=%s", to, payload.get("sid"), payload.get("status"))
    return payload
