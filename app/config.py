import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = lambda *a, **k: None

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
MENU_FILE = DATA_DIR / "menu.json"
# Vercel's filesystem is read-only except /tmp, and /tmp is wiped between
# invocations: fine for a look around, not for real orders (see /guide).
ON_VERCEL = bool(os.environ.get("VERCEL"))
DB_FILE = Path("/tmp/kitchen.db") if ON_VERCEL else DATA_DIR / "kitchen.db"

# The kitchen screen password. If unset, a random one is generated for this run
# and printed at startup, so there is never a well-known default.
import secrets as _secrets
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or _secrets.token_urlsafe(12)
if not os.environ.get("ADMIN_TOKEN"):
    print(f"[config] ADMIN_TOKEN not set; using a temporary one for this run: {ADMIN_TOKEN}")

# Delivery config (from spec §7, real distance data)
# Fixed delivery fee per distance band (Oct 2026): charged at checkout,
# inside the total, so the customer knows the price before ordering. The
# kitchen pays the actual Porter fare and absorbs the difference; the owner's fare
# reply is stored as courier_cost so the bands can be tuned from real data.
# One minimum everywhere, set so a single Mini Thali (₹188) can be delivered.
MIN_ORDER = int(os.environ.get("MIN_ORDER", "180"))
DELIVERY_ZONES = [
    {"name": "within 3 km", "max_km": 3.0, "fee": 49, "min_order": MIN_ORDER},
    {"name": "3-5 km", "max_km": 5.0, "fee": 69, "min_order": MIN_ORDER},
    {"name": "5-7 km", "max_km": 7.0, "fee": 89, "min_order": MIN_ORDER},
]
# Set DELIVERY_ZONES_JSON to replace the bands without a code change, e.g.
# '[{"name":"within 4 km","max_km":4,"fee":40},{"name":"4-8 km","max_km":8,"fee":80}]'.
# Bands must be listed nearest first; min_order defaults to MIN_ORDER.
if os.environ.get("DELIVERY_ZONES_JSON"):
    import json as _json
    DELIVERY_ZONES = [
        {"min_order": MIN_ORDER, **z, "max_km": float(z["max_km"])}
        for z in _json.loads(os.environ["DELIVERY_ZONES_JSON"])
    ]
# What the delivery page says about each band, by position in DELIVERY_ZONES.
# A band given in DELIVERY_ZONES_JSON can carry its own "areas" and "ride" keys instead.
ZONE_COPY = [
    {"areas": "Closest neighbourhoods", "ride": "15–25 min ride"},
    {"areas": "Nearby neighbourhoods", "ride": "20–30 min ride"},
    {"areas": "Farther neighbourhoods", "ride": "30–40 min ride"},
]
# Outer edge of the delivery area, derived so copy never disagrees with the bands.
DELIVERY_RADIUS_KM = DELIVERY_ZONES[-1]["max_km"]

FREE_DELIVERY_ABOVE = 700  # optional: free delivery over this amount
# Free-delivery promo is PAUSED for now (per owner). When re-enabled, zone
# fees over FREE_DELIVERY_ABOVE get waived again ("free over ₹700").
FREE_DELIVERY_ENABLED = False

# Order windows instead of a rigid single slot. The kitchen commits to *arrive
# by* the end of the chosen window; scheduled_at stores the window's reference
# time (mid) so sorting/kitchen order stays sane. Tweak the times freely.
DELIVERY_WINDOWS = [
    {"id": "lunch",  "label": "Lunch window",  "text": "12:00 – 2:30 PM", "ref": (13, 0)},
    {"id": "dinner", "label": "Dinner window", "text": "6:30 – 9:30 PM",  "ref": (19, 45)},
]

# GST for direct (non-aggregator) orders. Aggregator data shows a consistent 5%
# charged on every order (the standard non-ITC restaurant rate) — aggregators
# remit that themselves under Section 9(5), which doesn't cover direct sales,
# so this app has to collect it itself. Confirm the rate/registration status
# with your CA before changing — this mirrors observed data, not tax advice.
GST_RATE = float(os.environ.get("GST_RATE", "0.05"))
GST_ENABLED = os.environ.get("GST_ENABLED", "1") not in ("0", "false", "False")

# Packing charge for direct orders — flat, with a step up for large orders.
# Mirrors the existing "Container Charge" already used elsewhere in the
# business; retune these two numbers freely, they're a starting estimate.
PACKING_FEE = int(os.environ.get("PACKING_FEE", "20"))
PACKING_FEE_LARGE_ORDER_THRESHOLD = int(os.environ.get("PACKING_FEE_LARGE_ORDER_THRESHOLD", "1000"))
PACKING_FEE_LARGE_ORDER = int(os.environ.get("PACKING_FEE_LARGE_ORDER", "40"))

# GSTIN — shown on order confirmations/receipts once set. Blank until filled in.
GSTIN = os.environ.get("GSTIN", "")

# Monday is a half day — kitchen doesn't take online orders until the
# afternoon. "Second half of the day" is a placeholder for 14:00 IST;
# confirm the exact reopen time with the owner and adjust if it's off.
MONDAY_OPENS_AT = "14:00"

# Store identity — single source of truth for FAQ answers and page copy, so
# hours/address/phone can never drift between the visible page and schema.
from .business import business as _biz  # noqa: E402

STORE_NAME = _biz.name
STORE_ADDRESS = _biz.address
STORE_PICKUP_LINE = _biz.pickup_line
STORE_WHATSAPP = _biz.phone_display  # the kitchen's own number (calls + WhatsApp)
# The ordering bot's number (Cloud API): website "Order on WhatsApp" and
# order-question links open a chat with it; it relays questions to the owner.
WHATSAPP_ORDER_NUMBER = os.environ.get("WHATSAPP_ORDER_NUMBER", "")
WHATSAPP_ORDER_DISPLAY = os.environ.get("WHATSAPP_ORDER_DISPLAY", "")
OPENING_HOURS = {
    "mon_sat": ("09:00", "21:00"),
    "sunday": ("11:00", "21:00"),
}

ORDER_STATUSES = [
    "new",
    "preparing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
]

# WhatsApp Cloud API (optional — bot runs in dry-run/log mode without these)
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
# WhatsApp Business Account id (WhatsApp Manager > Account tools > Phone
# numbers, or the "WhatsApp Business Account ID" in Business settings).
# Only needed to list/check templates: GET /api/admin/whatsapp/templates.
WHATSAPP_WABA_ID = os.environ.get("WHATSAPP_WABA_ID", "")
# Meta business portfolio that owns the WhatsApp
# accounts and the Menu catalogue; used to find the WABA of WHATSAPP_PHONE_ID.
META_BUSINESS_ID = os.environ.get("META_BUSINESS_ID", "")
# The "Menu" catalogue (fed hourly from /whatsapp-catalog.csv), linked to the
# WABA of the ordering number; product_list messages name it explicitly.
WHATSAPP_CATALOG_ID = os.environ.get("WHATSAPP_CATALOG_ID", "")
# Published checkout Flow (app/whatsapp/flows/checkout.json) on the ordering
# number's WABA. Empty: the bot checks out step by step with buttons instead.
WHATSAPP_CHECKOUT_FLOW_ID = os.environ.get("WHATSAPP_CHECKOUT_FLOW_ID", "")
WHATSAPP_FLOW_MODE = os.environ.get("WHATSAPP_FLOW_MODE", "")  # "draft" while testing
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
WHATSAPP_GRAPH_VERSION = "v26.0"
WHATSAPP_GRAPH_URL = "https://graph.facebook.com"

# If set, the bot appends replies here instead of sending to WhatsApp (dev mode)
WHATSAPP_DRY_LOG = os.environ.get("WHATSAPP_DRY_LOG", "data/whatsapp.log")

# Master switch for outbound WhatsApp. Set to "1" once Meta business verification
# completes and the number is registered; until then, status notifications are
# routed to the SMS fallback (Twilio). Flip this (no code change) to go live.
WHATSAPP_ACTIVE = os.environ.get("WHATSAPP_ACTIVE", "") == "1"

# Twilio SMS fallback (used when WhatsApp templates/number aren't verified yet).
# Auth via API Key SID + secret, scoped to an Account SID (SDK pattern).
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_ID", "")   # AC...
TWILIO_API_KEY = os.environ.get("TWILIO_SID", "")              # SK...
TWILIO_API_SECRET = os.environ.get("TWILIO_CLIENT_SECRET", "")
TWILIO_FROM = os.environ.get("TWILIO_FROM", "")                # E.164 sender / sender ID
# Trial accounts (verified recipients + pre-defined templates only) block custom
# bodies with error 572006 and only deliver to verified numbers — so on trial the
# client SKIPS (can't send meaningful order SMS). Set to "1" while on the free
# trial; flip to "" after upgrading to a paid account with an Indian DLT sender
# ID for real free-form order-status SMS.
TWILIO_TRIAL = os.environ.get("TWILIO_TRIAL", "1") == "1"
TWILIO_API_URL = "https://api.twilio.com"

# Telegram admin-alert on new orders (no Meta, no sender-ID). Sends the owner a message
# when an order lands so she knows without opening admin. TELEGRAM_BOT_TOKEN from
# @BotFather; TELEGRAM_CHAT_ID is her bot-chat id (talk to @userinfobot to get it).
# Empty token = disabled; the admin page beep remains the fallback.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Static UPI payment collection (no gateway needed). When a customer pays by UPI,
# we hand them a upi:// deep link (and QR) pre-filled with your VPA and the
# order amount; the credit is confirmed manually by the kitchen. UPI_VPA is the
# only required setting (e.g. "yourname@bank"); UPI_PAYEE_NAME shows on the
# pay screen. Empty VPA = UPI hidden at checkout (COD only).
UPI_VPA = os.environ.get("UPI_VPA", "")
UPI_PAYEE_NAME = os.environ.get("UPI_PAYEE_NAME", _biz.name)
UPI_TXN_NOTE = os.environ.get("UPI_TXN_NOTE", f"{_biz.name} order payment")


def upi_uri(amount: float | int, txn_ref: str, payee_name: str | None = None) -> str:
    """Build a UPI Intent deep-link the customer's UPI app can open.

    Uses the standard UPI Intent spec (pa/pn/am/cu/tn/tr). The transaction
    reference doubles as the order id so a UPI payment notice can be matched.
    """
    from urllib.parse import quote
    pn = payee_name or UPI_PAYEE_NAME
    tr = quote(txn_ref)
    tn = quote(UPI_TXN_NOTE)
    return (
        f"upi://pay?pa={UPI_VPA}&pn={quote(pn)}&am={amount}&cu=INR"
        f"&tn={tn}&tr={tr}"
    )

# Shiprocket Quick (hyperlocal delivery) — credentials for API auth
SHIPROCKET_API_EMAIL = os.environ.get("SHIPROCKET_API_EMAIL", "")
SHIPROCKET_API_PASSWORD = os.environ.get("SHIPROCKET_API_PASSWORD", "")

# the owner's WhatsApp phone number — messages from this number trigger admin commands
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "")

# Public base URL for admin links (used in Telegram kitchen alerts).
SITE_URL = os.environ.get("SITE_URL", _biz.site_url)

# Google Business Profile "ask for review" short link
GOOGLE_REVIEW_LINK = os.environ.get("GOOGLE_REVIEW_LINK", "")

# Google Places API (New) — server-side key, optional; live rating/review
# count on /about. Cached server-side (see app/reviews.py) so this stays
# well inside the 5,000 free Place Details Pro calls/month. Unset = feature
# just stays hidden. (Not the same key as GOOGLE_MAPS_JS_API_KEY below —
# this one should be IP-restricted, not exposed to browsers.)
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
GOOGLE_PLACE_ID = os.environ.get("GOOGLE_PLACE_ID", "")

# Google Maps JavaScript API — client-side key, optional; powers the address
# autocomplete + map pin in checkout. This key IS meant to be visible in
# page source (that's how Maps JS API keys work) — restrict it by HTTP
# referrer to your domain in Google Cloud Console, not by keeping it
# secret. Unset = checkout falls back to a plain address textarea.
GOOGLE_MAPS_JS_API_KEY = os.environ.get("GOOGLE_MAP_API_KEY", "")
