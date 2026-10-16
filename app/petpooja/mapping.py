"""Pure conversions between our order/menu shapes and Petpooja's payloads.

Field names and nesting here follow the Sep 2026 "API Guide for Placing
Orders on Petpooja POS" (temp/API Guide for Placing Orders on Petpooja
POS.pdf), Petpooja's newer worked example, which disagrees in a couple of
spots with the older Apiary docs at https://onlineorderingapisv210.docs.apiary.io
— notably order-level `Tax.details` (required, was previously sent empty)
and `addon_items` as a flat list (Apiary nests it as `AddonItem.details`).
Went with the newer guide since it's what Petpooja pointed at for the
current sandbox review; flip back to the Apiary shape if the sandbox
rejects it.

RESOLVED (Sep 2026, §3.2 reconciliation landed): OrderItem.id used to be our
own menu item_id (the data/menu.json slug)
instead of Petpooja's real catalog id — Save Order was accepted (success=1)
but items didn't exist in the POS catalogue, so orders were invisible on the
dashboard. Now app/petpooja/catalog.py resolves every slug to the catalogue's
real `itemid` (the id Petpooja itself pushes in Menu Trigger, cached in
data/petpooja_menu_raw.json), keyed by item name, and falls back to the slug
with a warning if unmapped. Half portions share the base item's id.

Also approximate, flagged inline: per-item/per-charge GST breakdown. Our
order model keeps a single `gst_amount` on the order (see app/orders.py
gst_for()); Petpooja wants tax itemised per line and per charge
(service/delivery/packing). We prorate the order-level GST across items by
price share and split each into CGST/SGST halves, which is standard for
intra-state GST but is a derived approximation, not sourced from a real
per-item tax table. Confirm this reconciles with Petpooja's own tax setup
for the restaurant before relying on it for filing.
"""
import re
from datetime import datetime, timedelta, timezone

from .config import (
    PETPOOJA_REST_ID,
    PETPOOJA_RES_ADDRESS,
    PETPOOJA_RES_CONTACT,
    PETPOOJA_RES_NAME,
)

# Petpooja Order Callback status codes -> our internal order status.
# [-1 = Cancelled, 1/2/3 = Accepted, 4 = Dispatch, 5 = Food Ready, 10 = Delivered]
CALLBACK_STATUS_MAP = {
    "-1": "cancelled",
    "1": "preparing",
    "2": "preparing",
    "3": "preparing",
    "4": "out_for_delivery",
    "5": "ready",
    "10": "delivered",
}

# order_type letter code inferred from the Save Order example ("H" for a
# home-delivery sample order) — not spelled out anywhere in the docs
# reference prose. Verify against a real call; Petpooja support can confirm
# the full code list (dine-in is presumably "D").
_ORDER_TYPE_CODE = {"delivery": "H", "pickup": "P"}

# payment_type inferred similarly from the "COD" example value.
_PAYMENT_TYPE_CODE = {"cod": "COD", "upi": "ONLINE"}


_IST = timezone(timedelta(hours=5, minutes=30))


def _ist(created_at: str | None) -> str:
    """Our created_at is SQLite datetime('now') = UTC; the POS wants local
    (IST) "YYYY-MM-DD HH:MM:SS". Until Oct 2026 the raw UTC string went out,
    so every order reached the terminal looking 5.5 h old."""
    try:
        dt = datetime.fromisoformat((created_at or "").replace("T", " ").split(".")[0])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M:%S")


def _clean(text, limit: int = 250) -> str:
    """Single-line, trimmed text: newlines/tabs and stray control chars in a
    customer's address or note are a classic way to break a POS receipt."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return re.sub(r"\s{2,}", " ", s).strip()[:limit]


def _phone10(phone) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def _split_cgst_sgst(amount: float, rate_pct: float, tax_ids: dict[str, str]) -> list[dict]:
    # ids are the POS catalogue's real tax ids (catalog.tax_ids()), NOT
    # anything derived from our item slug: until Oct 2026 this sent
    # "<slug>-c"/"<slug>-s", ids the POS has never heard of.
    half = round(amount / 2, 2)
    return [
        {"id": tax_ids["CGST"], "name": "CGST", "tax_percentage": f"{rate_pct / 2:.2f}", "amount": f"{half:.2f}"},
        {"id": tax_ids["SGST"], "name": "SGST", "tax_percentage": f"{rate_pct / 2:.2f}", "amount": f"{half:.2f}"},
    ]


def _order_level_tax_details(gst_amount: float, rate_pct: float, tax_ids: dict[str, str]) -> list[dict]:
    """Order-level `Tax.details` — a separate, required breakdown from the
    per-item `item_tax` above (see the "Tax & Discounts" section of the Sep
    2026 Save Order API guide in temp/). restaurant_liable_amt mirrors the
    tax amount since gst_liability is "restaurant" for every item here."""
    half_amt = round(gst_amount / 2, 2)
    half_rate = round(rate_pct / 2, 2)
    return [
        {"id": tax_ids["CGST"], "title": "CGST", "type": "P", "price": f"{half_rate}%",
         "tax": f"{half_amt:.2f}", "restaurant_liable_amt": f"{half_amt:.2f}"},
        {"id": tax_ids["SGST"], "title": "SGST", "type": "P", "price": f"{half_rate}%",
         "tax": f"{half_amt:.2f}", "restaurant_liable_amt": f"{half_amt:.2f}"},
    ]


def order_to_save_order_payload(order: dict, callback_url: str, gst_rate: float) -> dict:
    """Build the `orderinfo` body for POST save_order from our `db.get_order()` row.

    `order` is the dict returned by app.db.get_order() (has `items`, totals,
    customer_name/phone, delivery_* fields). `gst_rate` is app.config.GST_RATE
    (e.g. 0.05), used only to label the prorated per-item tax percentage.
    """
    subtotal = float(order["subtotal"])
    packing_fee = float(order.get("packing_fee") or 0)
    delivery_fee = float(order.get("delivery_fee") or 0)
    gst_amount = float(order.get("gst_amount") or 0)
    total = float(order["total"])
    order_type = order.get("order_type", "delivery")
    payment_method = (order.get("payment_method") or "cod").lower()

    # Prorate order-level GST across items by price share (see module docstring).
    # Petpooja's default: an order discount comes off the items before tax,
    # so the taxable base (and each item's share) is net of the discount.
    order_discount = float(order.get("discount_total") or 0)
    net_factor = (1 - order_discount / subtotal) if subtotal else 1
    taxable_base = subtotal - order_discount + packing_fee
    from .catalog import map_order_items, tax_ids as catalog_tax_ids
    mapped_items = map_order_items(order["items"])
    tax_ids = catalog_tax_ids()
    unmapped = [it["name"] for it in mapped_items if not str(it["petpooja_item_id"]).isdigit()]
    if unmapped:
        # Never send an id the POS catalogue doesn't have (it can't render
        # the line). Checkout blocks these items upstream; this is the guard.
        raise ValueError(f"items not on the Petpooja POS catalogue: {', '.join(unmapped)}")
    item_lines = []
    for it in mapped_items:
        unit_price = float(it["price"])
        line_total = unit_price * float(it["qty"])
        share = (line_total * net_factor / taxable_base) if taxable_base else 0
        item_gst = round(gst_amount * share, 2)
        # price/final_price are per single unit, even at qty > 1 — the
        # doc's worked example is explicit that quantity is NOT multiplied
        # in here (the qty-scaled total is carried separately in the
        # order-level `total` key). final_price = price - item_discount.
        item_discount = float(it.get("item_discount") or 0)
        final_price = unit_price - item_discount
        is_half = str(it.get("item_id", "")).endswith("__half")
        item_lines.append({
            "id": it["petpooja_item_id"],  # real catalogue id (see app/petpooja/catalog.py)
            # The POS's own item name (exact catalogue spelling); a half
            # portion has no POS variation, so it rides as the base item at
            # the half price with the portion spelled out in `description`.
            "name": _clean(it.get("petpooja_name") or it["name"], 100),
            # Mirror the catalogue's own tax treatment per item (Petpooja
            # pushes `tax_inclusive: true` on these POS items): telling the
            # POS "inclusive" stops it materializing GST again on top of the
            # exact total we already collected from the customer. The item
            # 'price' sent here stays as our checkout price and the tax
            # lines below still reflect the GST line our customer actually
            # paid — matching Petpooja's flag without changing the money.
            "tax_inclusive": it.get("petpooja_tax_inclusive", False),
            "gst_liability": "restaurant",
            "item_tax": _split_cgst_sgst(item_gst, gst_rate * 100, tax_ids),
            "tax_percentage": f"{gst_rate * 100:.2f}",
            "item_discount": f"{item_discount:.2f}",
            "price": f"{unit_price:.2f}",
            "final_price": f"{final_price:.2f}",
            "quantity": str(it["qty"]),
            "description": "Half portion" if is_half else "",
            "variation_name": it.get("variation_name", ""),
            "variation_id": str(it.get("variation_id", "")),
            # Sent under BOTH keys: the flat "addon_items" key alone (new
            # guide) was confirmed silently dropped on a real sandbox order
            # (Sep 2026) — item saved fine but the addon never appeared on
            # the receipt. Sending it nested under "AddonItem.details" too
            # (the older Apiary shape) fixed it — receipt then showed the
            # addon and the correct addon-inclusive total. Never isolated
            # which key Petpooja's backend actually reads since both were
            # sent together; harmless to keep sending both indefinitely.
            "addon_items": it.get("addon_items", []),
            "AddonItem": {"details": it.get("addon_items", [])},
        })

    created_on = _ist(order.get("created_at"))
    # advanced_order is always "N" (we don't support scheduled-ahead orders)
    # — the doc is explicit that preorder_date/time must then mirror
    # created_on, not be left blank.
    preorder_date, _, preorder_time = created_on.partition(" ")

    # Packing charge IS folded into our taxable base (see gst_for()'s
    # docstring), so its share of gst_amount is real, not zero. Prorate the
    # same way item_gst is prorated above — required field per the API
    # guide, previously hardcoded to "0" (docs/PETPOOJA_INTEGRATION.md §3.4).
    pc_tax_amount = round(gst_amount * (packing_fee / taxable_base), 2) if taxable_base else 0.0
    # Per the API guide: "Total = Item Final Price [- Order level Discount]
    # + GST [if liable by restaurant] + Packing Charges. The 'Total' should
    # only include the amount due to the restaurant" — delivery_charges is
    # explicitly excluded since that money belongs to Borzo, not us. This is
    # NOT the same number as our own order["total"] (which is the
    # customer-facing grand total, used below for collect_cash).
    restaurant_total = subtotal - order_discount + gst_amount + packing_fee

    order_details = {
        "orderID": str(order["id"]),
        "preorder_date": preorder_date,
        "preorder_time": preorder_time,
        "service_charge": "0",
        "sc_tax_amount": "0",
        "delivery_charges": f"{delivery_fee:.2f}",
        # Genuinely 0, not an oversight: gst_for() explicitly keeps delivery
        # fee outside the taxable base (it's a Borzo pass-through, not a
        # restaurant charge) — unlike pc_tax_percentage below, which isn't.
        "dc_tax_percentage": "0",
        "dc_tax_amount": "0",
        # Present in Petpooja's official example but absent from our payload
        # until now — the per-charge liability breakdown their guide's field
        # table lists alongside the flat dc_tax_amount/pc_tax_amount above.
        # Delivery is untaxed in our model either way (see dc_tax_amount);
        # packing tax is ours to remit, so it's "restaurant", not "vendor".
        "dc_gst_details": [
            {"gst_liable": "vendor", "amount": "0"},
            {"gst_liable": "restaurant", "amount": "0"},
        ],
        "packing_charges": f"{packing_fee:.2f}",
        "pc_tax_percentage": f"{gst_rate * 100:.2f}",
        "pc_tax_amount": f"{pc_tax_amount:.2f}",
        "pc_gst_details": [
            {"gst_liable": "vendor", "amount": "0"},
            {"gst_liable": "restaurant", "amount": f"{pc_tax_amount:.2f}"},
        ],
        "order_type": _ORDER_TYPE_CODE.get(order_type, "H"),
        "advanced_order": "N",
        # urgent_time is documented as conditional — "if urgent, specify prep
        # time" (temp/api_guide.txt) — not a field to send unconditionally.
        # Sending it on every order regardless of urgent_order (as a prior
        # version of this code briefly did, 2026-09-19) is suspected of
        # causing a POS terminal to treat every normal order as urgent —
        # persistent ringing/instability on one client while another
        # (presumably reading the urgent_order boolean correctly instead)
        # handled the same order fine. Omit entirely; we have no concept of
        # urgent orders in our model.
        "urgent_order": False,
        "payment_type": _PAYMENT_TYPE_CODE.get(payment_method, "COD"),
        "table_no": "",
        "no_of_persons": "0",
        "discount_total": f"{order_discount:.2f}",
        "discount_type": order.get("discount_type", "F"),
        "tax_total": f"{gst_amount:.2f}",
        "total": f"{restaurant_total:.2f}",
        "description": _clean(order.get("instructions")),
        "created_on": created_on,
        # Petpooja's own field table is explicit: "0 = Third-party Rider,
        # 1 = Restaurant Rider" (temp/api_guide.txt) — their worked example
        # showing 1 for a delivery order was for an unknown restaurant's own
        # setup, not a universal convention, and briefly (2026-09-19) got
        # mistaken for one. Every delivery goes through Borzo, a
        # third-party courier, never restaurant staff — this must always be
        # 0. Also confirmed the hard way earlier: with `1 if order_type ==
        # "delivery" else 0`, sandbox receipts said "Home Delivery (Self
        # delivery)" — wrong on both counts.
        "enable_delivery": 0,
        "min_prep_time": 20,
        "callback_url": callback_url,
        "collect_cash": f"{total:.2f}" if payment_method == "cod" else "0",
        # Delivery-handoff OTP — present in Petpooja's example, no equivalent
        # in our model (we don't generate one). Blank rather than omitted,
        # since the field is otherwise documented as always present.
        "otp": "",
    }

    customer_details = {
        "email": "",
        "name": _clean(order.get("customer_name") or "Customer", 60),
        "address": _clean(order.get("delivery_address"), 400),
        "phone": _phone10(order.get("customer_phone")),
        "latitude": str(order.get("delivery_lat") or ""),
        "longitude": str(order.get("delivery_lng") or ""),
    }

    # Docs' Save Order example carries the order-level discount here as well as
    # in Order.details.discount_total. Previously always sent `[]` even when
    # discounted. `id` is a placeholder (same catalog-id reconciliation gap as
    # item ids, see module docstring); price mirrors discount_total.
    discount_lines = []
    if order_discount > 0:
        discount_lines.append({
            "id": "0",
            "title": order.get("discount_code") or "Discount",
            "type": order.get("discount_type", "F"),
            "price": f"{order_discount:.2f}",
        })

    return {
        "OrderInfo": {
            "Restaurant": {
                "details": {
                    "res_name": PETPOOJA_RES_NAME,
                    "address": PETPOOJA_RES_ADDRESS,
                    "contact_information": PETPOOJA_RES_CONTACT,
                    "restID": PETPOOJA_REST_ID,
                }
            },
            "Customer": {"details": customer_details},
            "Order": {"details": order_details},
            "OrderItem": {"details": item_lines},
            "Tax": {"details": _order_level_tax_details(gst_amount, gst_rate * 100, tax_ids)},
            "Discount": {"details": discount_lines},
        }
    }


def petpooja_status_to_order_status(status_code: str) -> str | None:
    """Map an Order Callback `status` code to our ORDER_STATUSES value."""
    return CALLBACK_STATUS_MAP.get(str(status_code))
