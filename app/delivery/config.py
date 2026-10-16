"""Delivery partner configuration — Borzo (primary) + Shiprocket (fallback)."""
import os

# Borzo (WeFast) API auth.
# Base URL is the CMS-module API, which (unlike the Business API) lets a normal
# account log in via the OpenCart wizard and obtain a production token with no
# support approval. Payload shape is identical to the Business API.
BORZO_AUTH_TOKEN = os.environ.get("BORZO_AUTH_TOKEN", "")
BORZO_BASE_URL = os.environ.get(
    "BORZO_BASE_URL",
    "https://robot.wefast.in/api/cms-module/1.0",  # production CMS-module API
)
BORZO_CALLBACK_TOKEN = os.environ.get("BORZO_CALLBACK_TOKEN", "")  # for webhook verification

# Shiprocket API auth (fallback — email + password for /auth/login)
SHIPROCKET_API_EMAIL = os.environ.get("SHIPROCKET_API_EMAIL", "")
SHIPROCKET_API_PASSWORD = os.environ.get("SHIPROCKET_API_PASSWORD", "")
SHIPROCKET_BASE_URL = "https://apiv2.shiprocket.in/v1/external"

# Pickup location (the kitchen)
from ..business import business as _biz  # noqa: E402

PICKUP_ADDRESS = _biz.address_full
PICKUP_PINCODE = _biz.pincode
PICKUP_LAT = 13.038909  # resolved from the Google Maps listing itself
PICKUP_LNG = 80.256394
PICKUP_PHONE = _biz.phone_ten  # restaurant contact for rider

# Default food parcel weight (kg) — used for serviceability & rate lookups
DEFAULT_WEIGHT_KG = 1.5

# Delivery fee safety: if quote exceeds this, flag for admin review
MAX_DELIVERY_FEE = 200

# Shiprocket channel ID for order-based tracking (fallback only).
# Find in Shiprocket Panel → Sales Channels.
SHIPROCKET_CHANNEL_ID = int(os.getenv("SHIPROCKET_CHANNEL_ID", "11901391"))
