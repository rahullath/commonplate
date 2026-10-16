"""Signed one-tap action links for the owner's Telegram order alerts.

She mostly doesn't touch the admin/kitchen UI — orders get handled via
Petpooja's own terminal, WhatsApp, or a phone call. Petpooja's status
callback already covers accept/ready/dispatch/cancel-via-terminal (see
app/petpooja/mapping.py CALLBACK_STATUS_MAP). What's NOT covered by any
system is (a) confirming a manually-collected UPI/COD payment and (b)
cancelling an order she never touches in Petpooja at all (e.g. customer
calls her directly). These links, embedded in the Telegram alert, cover
that gap without asking her to learn any UI.

Signed (HMAC, keyed on ADMIN_TOKEN) rather than random-and-stored so no new
DB column/lookup is needed — anyone with ADMIN_TOKEN could forge one
anyway, same trust boundary as the rest of /api/admin/*.
"""
import hashlib
import hmac

from .config import ADMIN_TOKEN


def sign(order_id: int, action: str) -> str:
    msg = f"{order_id}:{action}".encode()
    return hmac.new(ADMIN_TOKEN.encode(), msg, hashlib.sha256).hexdigest()[:16]


def verify(order_id: int, action: str, sig: str | None) -> bool:
    return hmac.compare_digest(sign(order_id, action), sig or "")
