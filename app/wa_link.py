"""Signed "Order now" links from WhatsApp to the web menu.

The bot sends <your site>/o/<token>; the token carries the
customer's WhatsApp number and an HMAC, so the menu can fill the number in
(and their saved address) without asking them to type it. Links last
LINK_DAYS; an expired or tampered token just opens the plain menu.
"""
import hashlib
import hmac
import os
import re
import time

from .business import business

SITE = business.site_url
LINK_DAYS = 14

_SECRET = (os.environ.get("WA_LINK_SECRET") or os.environ.get("WHATSAPP_APP_SECRET")
           or os.environ.get("APP_SECRET") or os.environ.get("WHATSAPP_ACCESS_TOKEN") or "")


def _sig(phone: str, day: int) -> str:
    return hmac.new(_SECRET.encode(), f"{phone}.{day}".encode(), hashlib.sha256).hexdigest()[:16]


def token(wa_id: str) -> str:
    phone = re.sub(r"\D", "", wa_id or "")
    day = int(time.time() // 86400)
    return f"{phone}.{day}.{_sig(phone, day)}"


def url(wa_id: str) -> str:
    return f"{SITE}/o/{token(wa_id)}" if _SECRET else f"{SITE}/menu"


def verify(tok: str | None) -> str | None:
    """The WhatsApp number (digits) a valid token was made for, else None."""
    if not (_SECRET and tok):
        return None
    m = re.fullmatch(r"(\d{8,15})\.(\d+)\.([0-9a-f]{16})", tok)
    if not m:
        return None
    phone, day = m.group(1), int(m.group(2))
    if not 0 <= time.time() // 86400 - day <= LINK_DAYS:
        return None
    return phone if hmac.compare_digest(m.group(3), _sig(phone, day)) else None
