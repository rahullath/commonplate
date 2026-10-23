"""WhatsApp/Meta product catalog feed, generated from data/menu.json on request.

Mirrors Meta's official sample catalog_products.csv column order so the
importer's field mapping needs no touch-ups. Half portions come through
menu.menu_for() as their own SKUs (<id>-half) at their own price — the
menu.json half_price field is a half-portion price, not a discount, so it is
NOT mapped to sale_price. Kept out of main.py so the feed stays buildable
without the web app importing back into it.
"""
import csv
import io
from pathlib import Path

from . import menu

from .business import business

SITE_URL = business.site_url
MENU_LINK = f"{SITE_URL}/menu"
BRAND = business.name
GOOGLE_CAT = "Food, Beverages & Tobacco > Food"
HALF_SUFFIX = "-half"
DISH_PHOTO_DIR = Path("app/static/img/dishes")
CARD_DIR = Path("app/static/img/cards")

HEADER = [
    "id", "title", "description", "availability", "condition", "link",
    "image_link", "brand", "price", "google_product_category",
    "fb_product_category", "quantity_to_sell_on_facebook", "sale_price",
    "sale_price_effective_date", "item_group_id", "gender", "color", "size",
    "age_group", "material", "pattern", "shipping", "shipping_weight",
    "offer_disclaimer", "offer_disclaimer_url", "video[0].url",
    "video[0].tag[0]", "gtin", "product_tags[0]", "product_tags[1]", "style[0]",
    "product_type",  # the menu category: Meta product sets (WhatsApp collections) filter on it
]

FILL_DESCRIPTIONS = {
    "mini-samosa": "Crisp, golden samosas with a spiced veg filling, served hot with chutney.",
    "aloo-dum": "Baby potatoes in a spicy onion-tomato gravy.",
    "aloo-gobi": "Dry-cooked potato and cauliflower sabzi, tempered with cumin.",
    "aloo-pakoda": "Potatoes coated in gram-flour batter and deep-fried.",
    "aloo-sabzi": "Comforting potato sabzi cooked in a mild onion-tomato gravy.",
    "aloo-sandwich": "Spiced potato filling grilled between bread slices.",
    "bhindi-ki-sabzi": "Okra sauteed with onions and mild spices, home-style.",
    "bread-chat": "Crisp bread pieces tossed with chaat chutneys, yogurt and sev.",
    "chilli-cheeesetoast": "Toasted bread topped with spiced chilli-cheese.",
    "churmur-chat": "Puffed rice mixed with spiced peas, onion, chutney and sev.",
    "curd-rice": "Steamed rice folded into cool tempered curd, with tadka.",
    "dahi-puri-6pcs": "Crisp puris topped with yogurt, chutneys and sev (6 pcs).",
    "extra-bhatura": "An extra fluffy bhatura to go with your chole.",
    "extra-pav": "An extra soft pav bun to go with your bhaji.",
    "kadai-ki-puri": "Crisp, flaky pooris served fresh off the tawa.",
    "raita-extra-curd": "Extra fresh curd to cool things down.",
    "water-bottle-300-ml": "Sealed 300 ml water bottle.",
}


def dish_photo_ids() -> set[str]:
    from . import photos
    bundled = {p.stem for p in DISH_PHOTO_DIR.glob("*.jpg")} if DISH_PHOTO_DIR.is_dir() else set()
    return bundled | photos.uploaded_ids()


def card_ids() -> set[str]:
    """Branded name cards (scripts/make_catalog_cards.py) for dishes with no
    photo yet: Meta rejects catalogue items without an image."""
    if not CARD_DIR.is_dir():
        return set()
    return {p.stem for p in CARD_DIR.glob("*.jpg")}


def _base(item_id: str) -> str:
    return item_id[: -len(HALF_SUFFIX)] if item_id.endswith(HALF_SUFFIX) else item_id


def _description(item: dict) -> str:
    desc = (item.get("description") or FILL_DESCRIPTIONS.get(_base(item["id"]), "")).strip()
    # Some POS descriptions are wrapped in [ ... } brackets.
    desc = desc.strip("[]{} ").replace("}", "").replace("{", "")
    if desc and desc[-1] not in ".!?":
        desc += "."
    if not desc:
        desc = f"A {(item.get('group') or 'house').lower()} dish, cooked to order."
    desc += " From the {BRAND} kitchen in {business.city}."
    if item["id"].endswith(HALF_SUFFIX):
        return "Half portion. " + desc
    return desc


def _link(item: dict) -> str:
    # Half SKUs get the id <base>-half inside the feed; point both at the
    # base dish page (only one canonical page per dish).
    return f"{SITE_URL}/menu/{_base(item['id'])}"


def _image(item: dict, photos: set[str], cards: set[str]) -> str:
    base = item.get("photo_id") or _base(item["id"])
    if base in photos:
        return f"{SITE_URL}/static/img/dishes/{base}.jpg"
    if base in cards:
        return f"{SITE_URL}/static/img/cards/{base}.jpg"
    return f"{SITE_URL}/static/img/landing-hero.jpg"


def _row(item: dict, photos: set[str], cards: set[str], off_pos: frozenset,
         has_half: bool) -> list[str]:
    base = _base(item["id"])
    # Off the POS = can't be ordered online at all. Daily "finished for
    # today" isn't reflected: Meta re-reads the feed only once a day.
    stock = "out of stock" if base in off_pos else "in stock"
    size = ("Half" if item["id"].endswith(HALF_SUFFIX) else "Full") if has_half else ""
    row = {
        "id": item["id"], "title": item["name"], "description": _description(item),
        "availability": stock, "condition": "new", "link": _link(item),
        "image_link": _image(item, photos, cards), "brand": BRAND,
        "price": f"{item['price']} INR", "google_product_category": GOOGLE_CAT,
        "item_group_id": base if has_half else "", "size": size,
        "product_tags[0]": item.get("group") or "", "product_tags[1]": "Pure veg",
        "product_type": item.get("group") or "",
    }
    return [row.get(col, "") for col in HEADER]


def _menu_in_priority_order() -> list[dict]:
    items = menu.load_menu()
    parents = sorted(
        (m for m in items if not m["id"].endswith(HALF_SUFFIX)),
        key=lambda m: (-(m.get("qty_2mo") or 0), (m.get("name") or "").lower()),
    )
    ordered: list[dict] = []
    for parent in parents:
        ordered.append(parent)
        if parent.get("half_price"):
            half = dict(parent)
            half["id"] = parent["id"] + HALF_SUFFIX
            half["name"] = f"{parent['name']} (Half)"
            half["price"] = parent["half_price"]
            half["popular"] = False
            half["photo_id"] = parent["id"]
            ordered.append(half)
    return ordered


def catalog_rows_prioritized() -> list[list[str]]:
    photos, cards, off_pos = dish_photo_ids(), card_ids(), menu._off_pos()
    items = _menu_in_priority_order()
    halves = {_base(i["id"]) for i in items if i["id"].endswith(HALF_SUFFIX)}
    return [_row(item, photos, cards, off_pos, _base(item["id"]) in halves) for item in items]


def catalog_rows() -> list[list[str]]:
    return catalog_rows_prioritized()


def catalog_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    w.writerows(catalog_rows())
    return buf.getvalue()