"""Petpooja Integration Platform API config — PetpoojaOnlineOrdering V2.1.0.

Credentials are issued by Petpooja per restaurant and per environment.
Everything here is blank/inert until they are set; setting the four
credential vars is what turns the integration on (see `app/orders.py` and
`app/webhooks.py`, both gated on PETPOOJA_APP_KEY).

Production endpoints differ from the sandbox defaults below: override all
three (save_order, update_order_status, rider_status_update) with the
environment variables in .env.example.

PETPOOJA_REST_ID must be the restaurant's *mapping code*, not the numeric
outlet id. With the numeric id Save Order still reports success, but the
order never reaches the POS terminal.
"""
from ..business import business
import os

PETPOOJA_APP_KEY = os.environ.get("PETPOOJA_APP_KEY", "")
PETPOOJA_APP_SECRET = os.environ.get("PETPOOJA_APP_SECRET", "")
PETPOOJA_ACCESS_TOKEN = os.environ.get("PETPOOJA_ACCESS_TOKEN", "")
PETPOOJA_REST_ID = os.environ.get("PETPOOJA_REST_ID", "")

# Outbound (we call Petpooja). All four staging endpoints share one API
# Gateway host — confirmed against the sandbox dashboard's "Configuration -
# Staging" page (Sep 2026); the save_order default previously pointed at a
# different, unconfirmed host from the old Apiary docs.
PETPOOJA_FETCH_MENU_URL = os.environ.get(
    "PETPOOJA_FETCH_MENU_URL",
    "https://qle1yy2ydc.execute-api.ap-southeast-1.amazonaws.com/V1/mapped_restaurant_menus",
)
PETPOOJA_SAVE_ORDER_URL = os.environ.get(
    "PETPOOJA_SAVE_ORDER_URL",
    "https://qle1yy2ydc.execute-api.ap-southeast-1.amazonaws.com/V1/save_order",
)
PETPOOJA_UPDATE_ORDER_STATUS_URL = os.environ.get(
    "PETPOOJA_UPDATE_ORDER_STATUS_URL",
    "https://qle1yy2ydc.execute-api.ap-southeast-1.amazonaws.com/V1/update_order_status",
)
PETPOOJA_RIDER_STATUS_URL = os.environ.get(
    "PETPOOJA_RIDER_STATUS_URL",
    "https://qle1yy2ydc.execute-api.ap-southeast-1.amazonaws.com/V1/rider_status_update",
)

# Inbound (Petpooja calls us): Push Menu, Order Callback, stock toggle, and
# store status aren't signed in the documented request shape, so we hand
# Petpooja URLs with this token baked in as a query param (?t=...) instead of
# trusting the payload alone. Generate a random value once credentials are
# being set up — see docs/HANDOFF.md.
PETPOOJA_WEBHOOK_TOKEN = os.environ.get("PETPOOJA_WEBHOOK_TOKEN", "")

# Optional HTTP(S) proxy for ALL outbound Petpooja calls (Save Order,
# menu fetch, status updates, rider status). Used so every order-placement
# request egresses from one static IP ("PETPOOJA_PROXY_URL" e.g.
# http://user:pass@proxy.example.com:3128) — Petpooja requires a static
# source IP for the live integration (see docs/PETPOOJA_INTEGRATION.md §3.7).
# Empty = direct connection (fine for the sandbox).
#
# NOTE: if the proxy password itself contains a "@", leave it UNencoded
# here — httpx splits userinfo on the last "@" and handles it correctly,
# and the stored env value (not curl) is what matters. (curl needs "%40".)
PETPOOJA_PROXY_URL = os.environ.get("PETPOOJA_PROXY_URL", "")

# Static restaurant info Save Order wants in the top-level request (not the
# OrderInfo/Restaurant object) — reuse what Borzo already has on file.
from ..delivery.config import PICKUP_ADDRESS, PICKUP_PHONE  # noqa: E402

PETPOOJA_RES_NAME = business.name
PETPOOJA_RES_ADDRESS = PICKUP_ADDRESS
PETPOOJA_RES_CONTACT = PICKUP_PHONE
