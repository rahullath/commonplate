import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from xml.sax.saxutils import escape as xml_escape
from pydantic import BaseModel, Field

from . import catalog, db, faqs, marketing, menu, orders, reviews, seo_aliases, wa_link
from .config import (
    ADMIN_TOKEN,
    DELIVERY_ZONES,
    FREE_DELIVERY_ABOVE,
    FREE_DELIVERY_ENABLED,
    GOOGLE_MAPS_JS_API_KEY,
    GOOGLE_REVIEW_LINK,
    GST_ENABLED,
    GST_RATE,
    PACKING_FEE,
    PACKING_FEE_LARGE_ORDER,
    PACKING_FEE_LARGE_ORDER_THRESHOLD,
)
from .config import ON_VERCEL, STORE_PICKUP_LINE, STORE_WHATSAPP, UPI_PAYEE_NAME, UPI_VPA
from .delivery.config import PICKUP_LAT, PICKUP_LNG

# uvicorn only configures its own loggers; without this every app-level
# INFO line (Petpooja save_order results, callbacks, store-status flips) was
# silently dropped in Railway logs — which is why POS incidents left no trace.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
# httpx logs every request URL at INFO — Google Maps calls carry the API key
# in the query string, so keep it at WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")

app = FastAPI(title="Direct ordering", version="0.2.0")

from .webhooks import router as webhook_router
from .qr import router as qr_router, init_qr

app.include_router(webhook_router)
app.include_router(qr_router)

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["aliases_for"] = seo_aliases.aliases_for
from . import third_party_ratings  # noqa: E402
templates.env.globals["external_rating"] = third_party_ratings.external


def wa_order_link(text: str = "Hi") -> str:
    """wa.me link to the ordering bot with a pre-filled first message."""
    from urllib.parse import quote as _q
    from .config import WHATSAPP_ORDER_NUMBER
    return f"https://wa.me/{WHATSAPP_ORDER_NUMBER}?text={_q(text)}"


templates.env.globals["wa_order_link"] = wa_order_link
from .config import WHATSAPP_ORDER_DISPLAY, WHATSAPP_ORDER_NUMBER  # noqa: E402
templates.env.globals["wa_order_number"] = WHATSAPP_ORDER_NUMBER
templates.env.globals["wa_order_display"] = WHATSAPP_ORDER_DISPLAY
from .business import business as biz  # noqa: E402
templates.env.globals["biz"] = biz
templates.env.globals["site_url"] = biz.site_url
templates.env.globals["on_vercel"] = ON_VERCEL
from .config import DELIVERY_RADIUS_KM, DELIVERY_WINDOWS  # noqa: E402
templates.env.globals["radius_km"] = f"{DELIVERY_RADIUS_KM:g}"
# {"lunch": {"text": "12:00 – 2:30 PM", "to": "12:00 to 2:30 PM"}, ...}
templates.env.globals["win"] = {
    w["id"]: {"text": w["text"], "to": w["text"].replace(" – ", " to ")} for w in DELIVERY_WINDOWS
}
def _promo():
    from . import discounts
    try:
        return discounts.public_offer()
    except Exception:
        return None
templates.env.globals["promo"] = _promo
app.mount("/static", StaticFiles(directory="app/static"), name="static")

DISH_PHOTO_DIR = Path("app/static/img/dishes")


def dish_photo_ids() -> set[str]:
    if not DISH_PHOTO_DIR.is_dir():
        return set()
    return {p.stem for p in DISH_PHOTO_DIR.glob("*.jpg")}


CATEGORY_HERO_DIR = Path("app/static/img/categories")

# Per-category hero alt text when the image needs describing (e.g. a poster
# with baked-in labels). Falls back to "<Group> at <business name>".
CATEGORY_HERO_ALT: dict[str, str] = {}


def category_hero(slug: str) -> dict | None:
    """Hero image for a category page, if present. Drop
    app/static/img/categories/<slug>.jpg (.png/.webp also work) to feature a
    category — no code change."""
    for ext in ("jpg", "jpeg", "png", "webp"):
        if (CATEGORY_HERO_DIR / f"{slug}.{ext}").is_file():
            group = next((g for g, s in group_slugs().items() if s == slug), slug)
            return {"src": f"/static/img/categories/{slug}.{ext}",
                    "alt": CATEGORY_HERO_ALT.get(slug, f"{group} at {business.name}")}
    return None


def _group_slug(group: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", group.lower()).strip("-")
    return slug or "uncategorised"


def group_slugs() -> dict[str, str]:
    """Stable group name -> URL slug map, sourced from the menu itself so the
    category pages, nav links and sitemap can never drift apart."""
    return {g: _group_slug(g) for g in dict.fromkeys(m["group"] for m in menu.load_menu())}


def all_categories() -> list[dict]:
    """Category descriptor list (name, slug, item count) for nav/sitemap."""
    groups: dict[str, list] = {}
    order: list[str] = []
    for m in menu.load_menu():
        g = m["group"]
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(m)
    return [
        {"name": g, "slug": _group_slug(g), "count": len(groups[g])}
        for g in order
    ]


UPDATES_FILE = Path("data/updates.json")

UPDATE_KIND_LABELS = {
    "instagram": "Instagram", "whatsapp": "WhatsApp status", "update": "Kitchen update",
    "offer": "Offer", "milestone": "Milestone",
}


def _image_orientation(src: str | None) -> str | None:
    """portrait/landscape for a /static/ image path, so templates can fit
    tall posters with contain instead of cropping them. None when unknown."""
    if not src or not src.startswith("/static/"):
        return None
    try:
        from PIL import Image
        with Image.open(Path("app/static") / src[len("/static/"):]) as im:
            w, h = im.size
        return "portrait" if h > w * 1.1 else "landscape"
    except Exception:
        return None


def _display_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return iso


def load_updates() -> list[dict]:
    """Compilation of Instagram/WhatsApp posts + kitchen announcements for the
    /updates page. File-backed so the owner adds entries without code changes;
    photo/dish references are validated so a typo can't break the page."""
    try:
        raw = json.loads(UPDATES_FILE.read_text())
    except (OSError, ValueError):
        return []
    photos = dish_photo_ids()
    entries = []
    for e in raw.get("entries", []):
        if not e.get("date") or not e.get("title") or not e.get("text"):
            continue
        dishes = [d for d in (e.get("dishes") or []) if menu.get_item(d)]
        entries.append({
            "date": e["date"],
            "date_display": _display_date(e["date"]),
            "kind": e.get("kind") or "update",
            "kind_label": UPDATE_KIND_LABELS.get(e.get("kind") or "update", "Update"),
            "title": e["title"],
            "text": e["text"],
            "photo": e["photo"] if e.get("photo") in photos else None,
            "image": e.get("image") or None,
            "image_alt": e.get("image_alt") or e["title"],
            "orientation": _image_orientation(e.get("image")),
            "dishes": [{"id": d, "name": menu.get_item(d)["name"]} for d in dishes],
            "source": e.get("source") or None,
            "source_label": e.get("source_label") or "See the original",
        })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries


CATEGORY_BLURBS: dict[str, str] = {
    "Thalis & Combos": "The whole meal on one plate — phulka, dal, sabzi, rice, curd, salad and a sweet, assembled fresh the moment you order. The North Indian Thali is the restaurant's signature; the Mini Thali is the same idea, half the size.",
    "Parathas & Breads": "Hand-rolled parathas off the tawa, served with curd and pickle. Punjab-style, stuffed parathas, and plain variants that sit beside any sabzi.",
    "Chaats & Snacks": "Made when you order so nothing goes soggy — papdi, dahi puri, vada pav, bhel, pav bhaji and the fried snacks that disappear first.",
    "Sabzi": "Home-style vegetable gravies and dry preparations cooked the same day — dal makhani, paneer butter masala, chole, and seasonal sabzis. Most come in half portions too.",
    "Starters": "Small plates for the table or a light meal — kebabs, tikkas and shallow-fried snacks served with the kitchen's chutneys.",
    "Soups & Rice": "Soups, rice dishes and the lightest things on the menu. Dal-rice and coriander rice are the everyday workhorses.",
    "Chai & Beverages": "Cutting chai, masala chai and thick shakes — the stuff people collect their orders for even when they stay for nothing else.",
    "Desserts": "The sweet that closes every thali, plus the kitchen's desserts. Gajar halwa and kheer show up by season.",
    "Italian": "Pure-veg Italian plates that fit the same kitchen — pastas and baked dishes without the 'fancy restaurant' markup.",
    "Specialities": "The dishes the kitchen is known for beyond the everyday menu — things people cross the city for.",
}


# ---- pages ----

# (item_id, tag, short description) — curated, shown on the home page "on the
# stove right now" strip. Falls back to the next entry if one is sold out.
TODAYS_PICKS = [
    ("north-indian-thali", "Today's thali", "Sabzi, dal tadka, phulka, rice and a sweet"),
    ("aloo-paratha", None, "Off the tawa, with curd and pickle"),
    ("paneer-butter-masala", None, "Fresh paneer in tomato and cashew gravy"),
    ("papdi-chat-6pcs", None, "Made when you order so it stays crisp"),
    ("chola-bhatura", None, "Fluffy bhatura with spiced chole"),
    ("dal-makhani", None, "Slow-cooked overnight, finished with cream"),
]


def _guide_response(request: Request):
    return templates.TemplateResponse(request, "guide.html", {
        "demo_url": marketing.DEMO_URL, "contact_email": marketing.CONTACT_EMAIL,
        "repo": marketing.SITE_REPO, "on_vercel": ON_VERCEL})


@app.get("/guide", response_class=HTMLResponse)
def guide_page(request: Request):
    """Deployment guide: where to host this, what to set, a live demo, who to ask."""
    return _guide_response(request)


@app.get("/", response_class=HTMLResponse)
def landing_page(request: Request):
    if ON_VERCEL:
        # A Vercel deployment can't keep orders (ephemeral disk), so its front
        # door is the project page: what commonplate is, a live demo, how to deploy.
        return templates.TemplateResponse(request, "commonplate.html",
                                          marketing.page_context(SITE_URL, "/"))
    photos = dish_photo_ids()
    picks = []
    for item_id, tag, desc in TODAYS_PICKS:
        item = menu.get_item(item_id)
        if item and menu.is_available(item_id):
            picks.append({**item, "tag": tag, "desc": desc, "has_photo": item_id in photos})
        if len(picks) == 4:
            break
    if len(picks) < 4:
        have = {p["id"] for p in picks}
        for m in menu.load_menu():
            if len(picks) == 4:
                break
            if (m.get("popular") and m["id"] not in have and not m["id"].endswith(menu.HALF_SUFFIX)
                    and menu.is_available(m["id"])):
                picks.append({**m, "tag": None, "desc": (m.get("description") or "").strip("[]"),
                              "has_photo": m["id"] in photos})
    google_stats = reviews.get_platform_stats().get("google")
    prices = [it["price"] for it in menu.load_menu() if isinstance(it.get("price"), (int, float))]
    price_range = (min(prices), max(prices)) if prices else (60, 350)
    faq_list = faqs.landing_faqs(DELIVERY_ZONES, FREE_DELIVERY_ABOVE, price_range, bool(UPI_VPA))
    return templates.TemplateResponse(
        request, "landing.html",
        {"picks": picks, "ld_graph": build_home_schema(faq_list), "google_stats": google_stats, "google_review_link": GOOGLE_REVIEW_LINK,
         "faqs": faq_list,
         "categories": all_categories(), "latest_updates": load_updates()[:3],
         "area_served": [{"@type": "Place", "name": f"{a}, {biz.city}"}
                         for a in seo_aliases.NEARBY_AREAS]},
    )


def build_home_schema(faq_list: list[dict]) -> dict:
    """One @graph for the home page: the Restaurant (the entity Google puts in
    the local pack and knowledge panel), the WebSite, and the FAQ block the
    page shows. Only facts the page itself states; nothing invented (no
    aggregateRating: the home page does not display one)."""
    def _hours(days, span):
        return {"@type": "OpeningHoursSpecification", "dayOfWeek": days,
                "opens": _to_24h(span[0]), "closes": _to_24h(span[1])}
    restaurant = {
        "@type": "Restaurant",
        "@id": f"{SITE_URL}/#restaurant",
        "name": biz.name,
        "description": f"{biz.tagline}. Order direct for delivery or pickup, no platform fees.",
        "url": f"{SITE_URL}/",
        "image": f"{SITE_URL}/static/logo.png",
        "logo": f"{SITE_URL}/static/logo.png",
        "telephone": biz.phone_e164,
        "email": biz.email,
        "servesCuisine": sorted({m["group"] for m in menu.load_menu() if m.get("group")})[:6],
        "priceRange": "₹",
        "menu": f"{SITE_URL}/menu",
        "acceptsReservations": False,
        "hasMenu": f"{SITE_URL}/menu",
        "address": {"@type": "PostalAddress", "streetAddress": biz.street_address,
                    "addressLocality": biz.city, "addressRegion": biz.state,
                    "postalCode": biz.pincode, "addressCountry": biz.country},
        "geo": {"@type": "GeoCoordinates", "latitude": PICKUP_LAT, "longitude": PICKUP_LNG},
        "openingHoursSpecification": [
            _hours(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"], biz.hours_mon_sat),
            _hours(["Sunday"], biz.hours_sunday),
        ],
        "potentialAction": {"@type": "OrderAction",
                            "target": {"@type": "EntryPoint", "urlTemplate": f"{SITE_URL}/menu",
                                       "actionPlatform": ["http://schema.org/DesktopWebPlatform",
                                                          "http://schema.org/MobileWebPlatform"]},
                            "deliveryMethod": ["http://purl.org/goodrelations/v1#DeliveryModeOwnFleet",
                                               "http://purl.org/goodrelations/v1#DeliveryModePickUp"]},
    }
    if biz.service_areas:
        restaurant["areaServed"] = [{"@type": "Place", "name": f"{a}, {biz.city}"} for a in biz.service_areas]
    same_as = [u for u in (f"https://instagram.com/{biz.instagram.lstrip('@')}" if biz.instagram else "",
                           GOOGLE_REVIEW_LINK) if u]
    if same_as:
        restaurant["sameAs"] = same_as
    graph = [
        restaurant,
        {"@type": "WebSite", "@id": f"{SITE_URL}/#website", "url": f"{SITE_URL}/",
         "name": biz.name, "inLanguage": "en", "publisher": {"@id": f"{SITE_URL}/#restaurant"}},
    ]
    if faq_list:
        graph.append({"@type": "FAQPage",
                      "mainEntity": [{"@type": "Question", "name": f["q"],
                                      "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
                                     for f in faq_list]})
    return {"@context": "https://schema.org", "@graph": graph}


def _to_24h(t: str) -> str:
    """'9 AM' / '9:30 PM' -> '09:00' / '21:30' (schema.org wants 24h)."""
    m = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([AaPp])", t or "")
    if not m:
        return "00:00"
    h, mi = int(m.group(1)) % 12, int(m.group(2) or 0)
    if m.group(3).lower() == "p":
        h += 12
    return f"{h:02d}:{mi:02d}"


def build_menu_schema(groups: list[dict]) -> dict:
    """Menu/MenuSection/MenuItem JSON-LD — mirrors exactly what menu.html
    renders (same groups, same items, same prices), so it always matches
    the visible page rather than drifting into its own thing."""
    return {
        "@context": "https://schema.org",
        "@type": "Menu",
        "name": f"{biz.name} menu",
        "url": f"{SITE_URL}/menu",
        "hasMenuSection": [
            {
                "@type": "MenuSection",
                "name": g["group"],
                "hasMenuItem": [
                    {
                        "@type": "MenuItem",
                        "name": it["name"],
                        **({"alternateName": a} if (a := seo_aliases.aliases_for(it["id"])) else {}),
                        "url": f"{SITE_URL}/menu/{it['id'].split('__')[0]}",
                        "offers": {
                            "@type": "Offer",
                            "price": str(it["price"]),
                            "priceCurrency": "INR",
                            "availability": "https://schema.org/InStock" if it["available"]
                            else "https://schema.org/OutOfStock",
                        },
                        "suitableForDiet": "https://schema.org/VegetarianDiet",
                    }
                    for it in g["items"]
                ],
            }
            for g in groups
        ],
    }


@app.get("/o/{token}")
def wa_order_link(token: str):
    """The bot's *Order now* button: the menu, signed in with their number."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/menu?wa={token}" if wa_link.verify(token) else "/menu", 302)


def _wa_customer(token: str | None) -> dict | None:
    phone = wa_link.verify(token)
    if not phone:
        return None
    phone = orders._normalize_phone(phone)
    name = (db.get_customer(phone) or {}).get("name") or ""
    if not name:
        try:
            from .whatsapp import sessions
            wa = phone if len(phone) > 10 else "91" + phone
            name = sessions.load(wa)["ctx"].get("profile_name") or ""
        except Exception:
            name = ""
    return {"phone": phone, "name": name}


@app.get("/menu", response_class=HTMLResponse)
def menu_page(request: Request, wa: str | None = None):
    groups = menu.grouped()
    for g in groups:
        g["slug"] = _group_slug(g["group"])
    return templates.TemplateResponse(
        request,
        "menu.html",
        {"groups": groups, "zones": DELIVERY_ZONES, "dish_photos": dish_photo_ids(),
         "pickup_lat": PICKUP_LAT, "pickup_lng": PICKUP_LNG,
         "google_maps_api_key": GOOGLE_MAPS_JS_API_KEY,
         "menu_schema": build_menu_schema(groups),
         "packing_fee": PACKING_FEE, "packing_fee_large": PACKING_FEE_LARGE_ORDER,
         "packing_fee_threshold": PACKING_FEE_LARGE_ORDER_THRESHOLD,
         "gst_rate": GST_RATE, "gst_enabled": GST_ENABLED,
         "upi_vpa": UPI_VPA, "upi_payee_name": UPI_PAYEE_NAME,
         "free_delivery_above": FREE_DELIVERY_ABOVE,
         "delivery_free_enabled": FREE_DELIVERY_ENABLED,
         "wa_customer": _wa_customer(wa),
         "categories": all_categories()},
    )

def _zone_rows() -> list[dict]:
    """One row per delivery band for the /delivery table."""
    from .config import ZONE_COPY
    rows, prev = [], None
    for i, z in enumerate(DELIVERY_ZONES):
        copy = ZONE_COPY[i] if i < len(ZONE_COPY) else {}
        km = f"{z['max_km']:g}"
        rows.append({
            "areas": z.get("areas") or copy.get("areas", ""),
            "ride": z.get("ride") or copy.get("ride", ""),
            "distance": f"Up to {km} km" if prev is None else f"{prev} to {km} km",
            "fee": z["fee"],
        })
        prev = km
    return rows


@app.get("/delivery", response_class=HTMLResponse)
def delivery_page(request: Request):
    return templates.TemplateResponse(
        request, "delivery.html",
        {"zones": DELIVERY_ZONES, "rows": _zone_rows(),
         "faqs": faqs.delivery_faqs(DELIVERY_ZONES, FREE_DELIVERY_ABOVE),
         "categories": all_categories()},
    )


@app.get("/how-to-order", response_class=HTMLResponse)
def how_to_order_page(request: Request):
    return templates.TemplateResponse(
        request, "how-to-order.html",
        {"zones": DELIVERY_ZONES, "packing_fee": PACKING_FEE,
         "packing_fee_large": PACKING_FEE_LARGE_ORDER,
         "packing_threshold": PACKING_FEE_LARGE_ORDER_THRESHOLD,
         "hours_text": faqs._open_text(), "store_phone": STORE_WHATSAPP,
         "pickup": STORE_PICKUP_LINE, "categories": all_categories()},
    )


@app.get("/menu/{item_id}", response_class=HTMLResponse)
def menu_item_page(request: Request, item_id: str):
    """One page per dish — each WhatsApp catalog item links here instead of the
    generic /menu. Half-portion ids (`<id>__half`) point at the same dish page."""
    base_id = menu._base_id(item_id) if item_id.endswith(menu.HALF_SUFFIX) else item_id
    item = menu.get_item(base_id)
    if not item:
        raise HTTPException(404, "Dish not found")
    item = dict(item)
    item["photo_id"] = item.get("photo_id") or base_id
    photos = dish_photo_ids()
    has_photo = item["photo_id"] in photos
    day = menu.today()
    available = menu.is_available(base_id, day)
    reason = menu._specialities_reason(item, day)

    # Same-group dishes first (strongest topical link), then pad with popular
    # items from elsewhere so every dish page has real internal links out.
    related = []
    for g in menu.grouped(day):
        if g["group"] == item["group"]:
            related = [it for it in g["items"]
                       if it["id"] != base_id and not it["id"].endswith(menu.HALF_SUFFIX)]
            break
    if len(related) < 4:
        seen = {base_id} | {r["id"] for r in related}
        for g in menu.grouped(day):
            for it in g["items"]:
                if len(related) >= 4:
                    break
                if it["id"] in seen or it["id"].endswith(menu.HALF_SUFFIX):
                    continue
                if it.get("popular"):
                    related.append(it)
                    seen.add(it["id"])
            if len(related) >= 4:
                break

    item_url = f"{SITE_URL}/menu/{base_id}"
    photo_url = f"{SITE_URL}/static/img/dishes/{item['photo_id']}.jpg" if has_photo else ""
    desc = (item.get("description")
            or catalog.FILL_DESCRIPTIONS.get(base_id)
            or f"{item['name']}, a {item['group'].lower()} from {biz.name}.")
    wa_link = f"https://wa.me/{WHATSAPP_ORDER_NUMBER}?text=" + quote(
        f"Hi {biz.name}, I'd like to order {item['name']} (₹{item['price']})."
    )

    status_text = "Available now" if available else (reason or "Finished for today")
    fees = ", ".join(f"₹{z['fee']} up to {z['max_km']:.0f} km" for z in DELIVERY_ZONES)
    min_order = min(z["min_order"] for z in DELIVERY_ZONES)
    ordering = {
        "zones": f"a fixed fee by distance ({fees}), shown before you pay. Minimum order ₹{min_order:g} for delivery; pickup has no minimum",
        "hours": biz.hours_spaced,
        "address": biz.address,
        "whatsapp": WHATSAPP_ORDER_DISPLAY,
        "phone": biz.phone_display,
    }
    breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": "Menu", "item": f"{SITE_URL}/menu"},
            {"@type": "ListItem", "position": 3, "name": item["group"],
             "item": f"{SITE_URL}/category/{_group_slug(item['group'])}"},
            {"@type": "ListItem", "position": 4, "name": item["name"], "item": item_url},
        ],
    }

    aliases = seo_aliases.aliases_for(base_id)
    dish_faqs = seo_aliases.item_faqs(item["name"], aliases, item["price"], item["group"])
    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": f["q"],
             "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
            for f in dish_faqs
        ],
    }

    schema = _item_product_schema(item, item_url, photo_url, desc, available, aliases)
    return templates.TemplateResponse(
        request,
        "item.html",
        {"item": item, "aliases": aliases, "dish_faqs": dish_faqs,
         "faq_schema": faq_schema, "nearby_areas": seo_aliases.NEARBY_AREAS, "has_photo": has_photo, "photo_url": photo_url,
         "available": available, "unavailable_reason": reason,
         "status_text": status_text, "half": item.get("half_price"),
         "related": related, "dish_photos": photos, "wa_link": wa_link,
         "item_url": item_url, "item_description": desc, "ordering": ordering,
         "category_slug": _group_slug(item["group"]),
         "categories": all_categories(),
         "item_schema": schema,
         "share_image": schema["image"],
         "breadcrumb_schema": breadcrumb,
         "dish_rating": db.dish_rating(base_id), "dish_reviews": db.dish_reviews(base_id)},
    )


def _item_product_schema(item: dict, item_url: str, photo_url: str,
                         desc: str, available: bool, aliases: list[str] = ()) -> dict:
    schema = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": item["name"],
        **({"alternateName": list(aliases)} if aliases else {}),
        "description": desc,
        "url": item_url,
        "category": item["group"],
        "brand": {"@type": "Brand", "name": biz.name},
        "suitableForDiet": "https://schema.org/VegetarianDiet",
        "offers": {
            "@type": "Offer",
            "priceCurrency": "INR",
            "price": str(item["price"]),
            "url": item_url,
            "itemCondition": "https://schema.org/NewCondition",
            "availability": ("https://schema.org/InStock" if available
                             else "https://schema.org/OutOfStock"),
            # Freshly cooked food can't be returned — state that explicitly
            # rather than leaving the field out, per Google's Merchant
            # structured-data requirements (Search Console flagged this as
            # missing, not as "no returns" — those read differently to
            # their validator).
            "hasMerchantReturnPolicy": {
                "@type": "MerchantReturnPolicy",
                "returnPolicyCategory": "https://schema.org/MerchantReturnNotPermitted",
                "applicableCountry": "IN",
            },
            # Real numbers, not placeholders: cheapest zone's delivery fee,
            # the actual ~7 km delivery radius from the kitchen (DELIVERY_ZONES'
            # outer band), and the same 30–60 min window quoted elsewhere on
            # the site.
            "shippingDetails": {
                "@type": "OfferShippingDetails",
                "shippingRate": {
                    "@type": "MonetaryAmount",
                    "value": str(DELIVERY_ZONES[0]["fee"]),
                    "currency": "INR",
                },
                "shippingDestination": {
                    "@type": "DefinedRegion",
                    "addressCountry": "IN",
                    "geoMidpoint": {
                        "@type": "GeoCoordinates",
                        "latitude": PICKUP_LAT,
                        "longitude": PICKUP_LNG,
                    },
                    "geoRadius": str(int(DELIVERY_ZONES[-1]["max_km"] * 1000)),
                },
                "deliveryTime": {
                    "@type": "ShippingDeliveryTime",
                    "handlingTime": {
                        "@type": "QuantitativeValue",
                        "minValue": 0, "maxValue": 0, "unitCode": "DAY",
                    },
                    "transitTime": {
                        "@type": "QuantitativeValue",
                        "minValue": 0.5, "maxValue": 1, "unitCode": "HUR",
                    },
                },
            },
        },
    }
    # Real per-dish ratings only (verified delivered orders, see
    # /api/orders/{ref}/review), and only when the page shows them too.
    rating = db.dish_rating(item["id"])
    if rating["count"]:
        schema["aggregateRating"] = {"@type": "AggregateRating",
                                     "ratingValue": str(rating["average"]),
                                     "ratingCount": rating["count"],
                                     "bestRating": "5", "worstRating": "1"}
        reviews = [{"@type": "Review",
                    "author": {"@type": "Person", "name": r["author_name"]},
                    "datePublished": (r["created_at"] or "")[:10],
                    "reviewBody": r["comment"],
                    "reviewRating": {"@type": "Rating", "ratingValue": str(r["rating"]),
                                     "bestRating": "5", "worstRating": "1"}}
                   for r in db.dish_reviews(item["id"]) if r.get("author_name")]
        if reviews:
            schema["review"] = reviews
    if photo_url:
        schema["image"] = photo_url
    elif (Path("app/static/img/cards") / f"{item['id']}.jpg").is_file():
        schema["image"] = f"{SITE_URL}/static/img/cards/{item['id']}.jpg"
    else:
        schema["image"] = f"{SITE_URL}/static/logo.png"
    return schema


@app.get("/track", response_class=HTMLResponse)
def track_landing_page(request: Request):
    """Order recovery / "My Orders". Primary path is device-local: orders
    placed from this browser are cached client-side (see menu.html) and
    fetched live by tracking token on load — no login, no reference number
    to remember. Phone lookup stays as the fallback for a new/changed
    device (see /api/orders/lookup)."""
    return templates.TemplateResponse(
        request, "track-landing.html",
        {"categories": all_categories(), "dish_photos": sorted(dish_photo_ids())},
    )


@app.get("/track/{ref}", response_class=HTMLResponse)
def track_page(request: Request, ref: str):
    """Customer tracking page. `ref` is the unguessable tracking token sent in
    the order confirmation; legacy numeric order ids still work (redirected to
    the canonical token URL) so old WhatsApp/SMS links keep resolving."""
    o = db.get_order_by_token(ref) if not ref.isdigit() else db.get_order(int(ref))
    if not o:
        raise HTTPException(404, "Order not found")
    token = o.get("tracking_token") or ref
    if not ref.isdigit():
        return templates.TemplateResponse(
            request, "track.html", {"order_id": o["id"], "tracking_token": token,
                                    "upi_vpa": UPI_VPA, "upi_payee_name": UPI_PAYEE_NAME,
                                    "categories": all_categories()}
        )
    return RedirectResponse(f"/track/{token}", status_code=301)


def upi_links(amount: float, order_id: int) -> dict:
    """UPI deep links for one payment: the generic upi:// intent (Android's
    app chooser, and what the QR encodes) plus app-specific schemes that
    also work on iPhone, where there is no generic chooser."""
    from urllib.parse import urlencode
    q = urlencode({"pa": UPI_VPA, "pn": UPI_PAYEE_NAME, "am": f"{amount:.2f}", "cu": "INR",
                   "tn": f"{biz.name} order {order_id}", "tr": f"ORD{order_id}"})
    return {"any": f"upi://pay?{q}", "gpay_android": f"tez://upi/pay?{q}",
            "gpay_ios": f"gpay://upi/pay?{q}", "phonepe": f"phonepe://pay?{q}",
            "paytm": f"paytmmp://pay?{q}"}


@app.get("/pay/{ref}", response_class=HTMLResponse)
def pay_page(request: Request, ref: str):
    """One-tap UPI payment for an order, by tracking token. The WhatsApp
    'Pay' button lands here: it opens the UPI app (Android chooser, or
    GPay / PhonePe / Paytm buttons), shows a QR for paying from another
    phone, and says so plainly when the order is paid, cash or cancelled."""
    o = db.get_order_by_token(ref) if not ref.isdigit() else None
    if not o:
        raise HTTPException(404, "Order not found")
    amount = float(o.get("total") or 0)
    if o.get("status") == "cancelled":
        state = "cancelled"
    elif o.get("paid"):
        state = "paid"
    elif (o.get("payment_method") or "cod") != "upi":
        state = "cash"
    elif not UPI_VPA:
        state = "no_upi"
    else:
        state = "due"
    items = [f"{int(i.get('qty') or 1)} × {i.get('name')}" for i in (o.get("items") or [])]
    items_text = ", ".join(items[:3]) + (f" and {len(items) - 3} more" if len(items) > 3 else "")
    links = upi_links(amount, o["id"])
    qr_svg = ""
    if state == "due":
        try:
            import segno
            qr_svg = segno.make(links["any"], error="m").svg_inline(scale=5, border=2)
        except Exception:
            log.exception("UPI QR failed for order %s", o["id"])
    return templates.TemplateResponse(request, "pay.html", {
        "order": o, "state": state, "amount_text": f"{amount:.2f}".removesuffix(".00"), "qr_svg": qr_svg,
        "payee": UPI_PAYEE_NAME, "vpa": UPI_VPA, "items_text": items_text or "your order",
        "links": links, "hide_nav": True})


@app.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse(
        request,
        "about.html",
        {
            "featured_reviews": reviews.list_featured_reviews(),
            "platform_stats": reviews.get_platform_stats(),
            "order_count": reviews.get_order_count_display(),
            "google_review_link": GOOGLE_REVIEW_LINK,
            "categories": all_categories(),
        },
    )


@app.get("/commonplate", response_class=HTMLResponse)
def commonplate_page(request: Request):
    """Project page: what commonplate is, for kitchens, riders and workers."""
    return templates.TemplateResponse(request, "commonplate.html",
                                      marketing.page_context(SITE_URL, "/" if ON_VERCEL else "/commonplate"))


@app.get("/updates", response_class=HTMLResponse)
def updates_page(request: Request):
    """Fresh from the Kitchen — compilation of Instagram/WhatsApp posts and
    kitchen announcements. File-backed (data/updates.json) so new entries land
    without code changes; gives crawlers fresh indexable content to chew on."""
    updates = load_updates()
    json_ld = {
        "@context": "https://schema.org",
        "@type": "Blog",
        "name": f"Fresh from the Kitchen — {biz.name} kitchen updates",
        "url": f"{SITE_URL}/updates",
        "blogPost": [
            {
                "@type": "BlogPosting",
                "headline": u["title"],
                "datePublished": u["date"],
                "articleBody": u["text"],
                **({"image": f"{SITE_URL}/static/img/dishes/{u['photo']}.jpg"} if u["photo"] else {}),
                "author": {"@type": "Organization", "name": biz.name},
            }
            for u in updates
        ],
    }
    return templates.TemplateResponse(
        request, "updates.html",
        {"updates": updates, "categories": all_categories(), "updates_json_ld": json_ld},
    )


@app.get("/updates.xml")
def updates_rss():
    """RSS feed of the same compilation — one more recrawl trigger for bots."""
    items = []
    for u in load_updates():
        link = u["source"] or f"{SITE_URL}/updates"
        items.append(
            f"  <item>\n    <title>{xml_escape(u['title'])}</title>\n"
            f"    <link>{xml_escape(link)}</link>\n"
            f"    <guid>{xml_escape(link)}#{u['date']}</guid>\n"
            f"    <pubDate>{u['date']}T06:00:00+05:30</pubDate>\n"
            f"    <description>{xml_escape(u['text'])}</description>\n  </item>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n<channel>\n'
        f"  <title>Fresh from the Kitchen — {biz.name}</title>\n"
        f"  <link>{SITE_URL}/updates</link>\n"
        f"  <description>Kitchen announcements from {biz.name}, {biz.locality}.</description>\n"
        + "\n".join(items) + "\n</channel>\n</rss>"
    )
    return Response(content=body, media_type="application/rss+xml")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse(
        request, "admin.html", {"groups": menu.grouped(), "hide_nav": True}
    )


@app.get("/kitchen.webmanifest")
def kitchen_manifest():
    """Separate install for the owner's phone: opens straight to the kitchen screen."""
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "name": f"{biz.name} kitchen", "short_name": "Kitchen", "id": "/kitchen",
        "start_url": "/kitchen?source=pwa", "scope": "/kitchen", "display": "standalone",
        "background_color": "#F6F3EC", "theme_color": "#0E7A45", "orientation": "portrait",
        "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, media_type="application/manifest+json")


@app.get("/api/admin/kitchen")
def admin_kitchen(x_admin_token: str | None = Header(None)):
    """Kitchen screen feed: open orders, today's finished ones, and the
    numbers the owner needs to book Porter and read the bill."""
    _check_admin(x_admin_token)
    from .delivery.config import PICKUP_ADDRESS, PICKUP_LAT, PICKUP_LNG, PICKUP_PHONE
    out = []
    for o in db.kitchen_orders():
        o = {k: v for k, v in o.items() if k not in ("sr_awb",)}
        o["track_url"] = f"{SITE_URL}/track/{o.get('tracking_token') or o['id']}"
        o["pay_url"] = f"{SITE_URL}/pay/{o.get('tracking_token') or o['id']}"
        o["feedback"] = db.get_feedback(o["id"])
        out.append(o)
    return {"orders": out, "store": db.get_store_status(), "kitchen": {
        "address": PICKUP_ADDRESS, "phone": PICKUP_PHONE, "lat": PICKUP_LAT, "lng": PICKUP_LNG,
        "zones": DELIVERY_ZONES}}


@app.post("/api/admin/orders/{order_id}/paid")
def admin_mark_paid(order_id: int, x_admin_token: str | None = Header(None)):
    """The kitchen saw the UPI payment: mark it and tell the customer."""
    _check_admin(x_admin_token)
    o = db.get_order(order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    if not o.get("paid"):
        db.mark_order_paid(order_id)
        from .notify import notify_payment_received
        notify_payment_received(db.get_order(order_id))
    return {"ok": True}


@app.get("/kitchen/o/{order_id}", response_class=HTMLResponse)
def kitchen_order_page(request: Request, order_id: int):
    """Open order button on the owner's WhatsApp alerts: the kitchen app, on that order."""
    return kitchen_page(request)


@app.get("/kitchen", response_class=HTMLResponse)
def kitchen_page(request: Request):
    # Stripped-down, one-purpose order screen for a kitchen tablet — no tabs,
    # no menu/chat/reviews, just live orders with one big action button each.
    return templates.TemplateResponse(request, "kitchen.html", {"hide_nav": True})


@app.get("/privacy-policy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {"categories": all_categories()})


@app.get("/refund-cancellation-policy", response_class=HTMLResponse)
def refund_cancellation_policy_page(request: Request):
    return templates.TemplateResponse(request, "refund-cancellation-policy.html", {"categories": all_categories()})


@app.get("/whatsapp-catalog.csv", response_class=PlainTextResponse)
def whatsapp_catalog_csv():
    return Response(content=catalog.catalog_csv(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=catalog.csv",
    })


# ---- SEO: verification, robots, sitemap ----

SITE_URL = biz.site_url


@app.get("/sw.js")
def service_worker():
    """Served at the root, not /static/sw.js — a service worker's default
    scope is the directory it's served from, and it needs to cover the
    whole site (/, /menu, etc.) to actually control page loads, not just
    /static/ assets."""
    return FileResponse("app/static/sw.js", media_type="application/javascript")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    lines = [
        "User-agent: *",
        "Disallow: /admin",
        "Disallow: /kitchen",
        "Disallow: /api/",
        "Disallow: /order-actions",
        "Disallow: /o/",
        "Disallow: /pay/",
        "Disallow: /track/",
        "",
        f"Sitemap: {SITE_URL}/sitemap.xml",
    ]
    return PlainTextResponse("\n".join(lines))


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt():
    menu_items = [m for m in menu.load_menu() if not m["id"].endswith(menu.HALF_SUFFIX)]
    popular = [m for m in menu_items if m.get("popular")][:8]
    prices = [float(m["price"]) for m in menu_items if m.get("price")]
    fees = "; ".join(f"₹{z['fee']} up to {z['max_km']:g} km (minimum order ₹{z['min_order']})"
                     for z in DELIVERY_ZONES)
    _win = templates.env.globals["win"]
    lines = [
        f"# {biz.name}",
        "",
        f"> {biz.tagline}. {biz.name} is on {biz.street}, {biz.locality}, {biz.city}, "
        "cooked to order and delivered direct from the kitchen.",
        "",
        f"- Address: {biz.address}, {biz.state}, {biz.country}",
        f"- Hours: {faqs._open_text()}.",
        f"- Order online: {SITE_URL}/menu",
        *([f"- Order on WhatsApp: {WHATSAPP_ORDER_DISPLAY} (send \"hi\")"] if WHATSAPP_ORDER_DISPLAY else []),
        f"- Kitchen phone, for calls and questions: {STORE_WHATSAPP}",
        f"- Delivery: fixed fee by distance from the kitchen: {fees}."
        + (" Areas: " + ", ".join(seo_aliases.NEARBY_AREAS) + "." if seo_aliases.NEARBY_AREAS else ""),
        f"- Pickup: {STORE_PICKUP_LINE}, usually ready in about 20 minutes",
        f"- Ordering ahead: lunch ({_win['lunch']['to']}) or dinner ({_win['dinner']['to']}) windows, up to 14 days ahead",
        f"- Payment: UPI (GPay, PhonePe, Paytm) or cash on delivery or pickup. Totals include packing (₹{PACKING_FEE}) and {GST_RATE * 100:g}% GST.",
        f"- Prices: ₹{min(prices):g} to ₹{max(prices):g} per dish",
        "",
        "## How to order",
        "",
        f"1. Open {SITE_URL}/menu, search or browse by section, and add dishes.",
        "2. Tap Checkout: phone number, name, delivery or pickup, and a time.",
        "3. For delivery, pick the address and drag the map pin onto the door. The pin sets the fee.",
        "4. Choose UPI or cash and place the order. A WhatsApp message follows with the order number and a tracking link.",
        f"Full guide: {SITE_URL}/how-to-order",
        "",
        "## Pages",
        "",
        f"- [Home]({SITE_URL}/): who we are, today's picks, ratings",
        f"- [Menu]({SITE_URL}/menu): today's dishes, prices and availability; order online",
        f"- [How to order]({SITE_URL}/how-to-order): website and WhatsApp ordering, payment, ordering ahead, cancelling",
        f"- Each dish has its own page, e.g. [Paneer Butter Masala]({SITE_URL}/menu/paneer-butter-masala); any menu item id works at {SITE_URL}/menu/<id>",
        f"- [Delivery]({SITE_URL}/delivery): delivery areas, fees and timing",
        f"- [Track your order]({SITE_URL}/track): look up an order by tracking reference or phone",
        f"- [About]({SITE_URL}/about): the kitchen's story, reviews and common questions",
        f"- [Kitchen updates]({SITE_URL}/updates): Instagram posts and kitchen announcements",
        f"- [Privacy policy]({SITE_URL}/privacy-policy)",
        f"- [Refund and cancellation policy]({SITE_URL}/refund-cancellation-policy)",
        "",
        "## Menu categories",
        "",
    ]
    for cat in all_categories():
        lines.append(f"- {cat['name']} ({cat['count']} items): {SITE_URL}/category/{cat['slug']}")
    lines.append(f"- [All categories]({SITE_URL}/category): the full category index in one page")
    lines += [
        "",
        "## Popular dishes",
        "",
    ]
    for m in popular:
        lines.append(f"- {m['name']}, ₹{m['price']}: {SITE_URL}/menu/{m['id']}")
    lines += [
        "",
        "## Dish names people also search for",
        "",
        "Same dish, other names or spellings, so a search for the alias finds the right page.",
        "",
    ]
    for m in menu_items:
        if aliases := seo_aliases.aliases_for(m["id"]):
            lines.append(f"- {m['name']} (₹{m['price']}), also called {', '.join(aliases)}: "
                         f"{SITE_URL}/menu/{m['id']}")
    return PlainTextResponse("\n".join(lines))


# Paths that render HTML but shouldn't be in the public sitemap.
SITEMAP_EXCLUDE = {"/admin", "/kitchen", "/404", "/track"}

# Optional path -> template file, just to attach a real <lastmod>. A page
# missing here still appears in the sitemap (via route auto-discovery below),
# just without a lastmod — so a new page can never silently fall out of the
# sitemap for want of an entry here.
SITEMAP_TEMPLATES = {
    "/": "landing.html",
    "/menu": "menu.html",
    "/delivery": "delivery.html",
    "/about": "about.html",
    "/privacy-policy": "privacy.html",
    "/refund-cancellation-policy": "refund-cancellation-policy.html",
    "/track": "track-landing.html",
    "/404": "404.html",
    "/updates": "updates.html",
    "/category": "categories.html",
    "/how-to-order": "how-to-order.html",
    "/commonplate": "commonplate.html",
    "/guide": "guide.html",
}


# Sitemap freshness for data-backed pages: lastmod follows the newest of the
# template and its data file, so new entries bump the date without a deploy.
SITEMAP_DATA_FILES = {
    "/updates": UPDATES_FILE,
    "/category": Path("data/menu.json"),
}


@app.get("/sitemap.xml")
def sitemap_xml():
    """Every GET page route that renders HTML, auto-discovered from FastAPI's
    route table — adding a new @app.get(..., response_class=HTMLResponse)
    page is enough for it to show up here, no separate list to remember."""
    template_dir = Path("app/templates")
    paths = sorted({
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.response_class is HTMLResponse
        and "GET" in route.methods
        and "{" not in route.path
        and route.path not in SITEMAP_EXCLUDE
    })
    if ON_VERCEL:
        paths = ["/", "/guide"]
    entries = []
    for path in paths:
        lastmod_tag = ""
        mtimes = []
        template_name = SITEMAP_TEMPLATES.get(path)
        if template_name:
            mtimes.append((template_dir / template_name).stat().st_mtime)
        data_file = SITEMAP_DATA_FILES.get(path)
        if data_file and data_file.exists():
            mtimes.append(data_file.stat().st_mtime)
        if mtimes:
            lastmod_tag = f"\n    <lastmod>{date.fromtimestamp(max(mtimes)).isoformat()}</lastmod>"
        entries.append(f"  <url>\n    <loc>{SITE_URL}{path}</loc>{lastmod_tag}\n  </url>")
    if ON_VERCEL:
        return Response(content='<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                        + "\n".join(entries) + "\n</urlset>", media_type="application/xml")
    menu_mtime = (Path("data/menu.json")).stat().st_mtime
    menu_items = [m for m in menu.load_menu() if not m["id"].endswith(menu.HALF_SUFFIX)]
    for m in menu_items:
        lastmod_tag = f"\n    <lastmod>{date.fromtimestamp(menu_mtime).isoformat()}</lastmod>"
        entries.append(f"  <url>\n    <loc>{SITE_URL}/menu/{m['id']}</loc>{lastmod_tag}\n  </url>")
    # Category archive pages carry the same freshness as the menu they list.
    for cat in all_categories():
        lastmod_tag = f"\n    <lastmod>{date.fromtimestamp(menu_mtime).isoformat()}</lastmod>"
        entries.append(
            f"  <url>\n    <loc>{SITE_URL}/category/{cat['slug']}</loc>{lastmod_tag}\n  </url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>"
    )
    return Response(content=body, media_type="application/xml")


# ---- menu API ----

@app.get("/api/menu")
def api_menu(day: str | None = None):
    return {"date": menu.today(day), "groups": menu.grouped(day)}


@app.get("/api/delivery")
def api_delivery(km: float, subtotal: float = 0.0):
    q = orders.delivery_fee(km, subtotal)
    if not q:
        return {"zone": "outside", "fee": None, "min_order": None}
    return {"zone": q["zone"], "fee": q["fee"], "min_order": q["min_order"]}


@app.get("/api/delivery/check")
def api_delivery_check(pincode: str):
    """Check if a pincode is serviceable by Shiprocket Quick."""
    result = orders.check_pincode_serviceable(pincode)
    return result


@app.get("/api/customer/{phone}")
def api_customer_info(phone: str):
    """Get saved address(es) for a customer (for checkout pre-fill)."""
    c = db.get_customer(phone)
    if not c:
        return {"exists": False, "address": None, "pincode": None, "name": None, "addresses": []}
    return {"exists": True, "address": c.get("address"), "pincode": c.get("pincode"),
            "name": c.get("name"), "addresses": db.list_customer_addresses(c["id"])}


# ---- availability (admin) ----

def _check_admin(token: str | None):
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid admin token")


class AvailabilityIn(BaseModel):
    available_ids: list[str] = Field(default_factory=list)
    unavailable_ids: list[str] = Field(default_factory=list)


@app.get("/api/admin/availability")
def admin_availability(day: str | None = None, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    day = menu.today(day)
    available = db.get_available_ids(day)
    return {"date": day, "available_ids": sorted(available), "last_day": db.last_available_day()}


@app.post("/api/admin/availability")
def admin_set_availability(body: AvailabilityIn, day: str | None = None,
                           x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    day = menu.today(day)
    db.set_availability(day, body.available_ids, body.unavailable_ids)
    return {"date": day, "ok": True}


@app.post("/api/admin/availability/repeat-yesterday")
def admin_repeat_yesterday(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    last = db.last_available_day()
    if not last:
        raise HTTPException(400, "No previously saved day to copy")
    today = date.today().isoformat()
    n = db.copy_availability(last, today)
    return {"ok": True, "copied_from": last, "copied_items": n}


# ---- shop open/closed, sold-out and hand-added dishes (kitchen screen) ----

_IST = timezone(timedelta(hours=5, minutes=30))


class StoreIn(BaseModel):
    is_open: bool
    mode: str = "manual"          # today | hours | manual (only when closing)
    hours: float | None = None
    reason: str | None = None


@app.get("/api/store-status")
def public_store_status():
    s = db.get_store_status()
    return {"is_open": s["is_open"], "reason": s["reason"], "turn_on_time": s["turn_on_time"]}


@app.post("/api/admin/store")
def admin_set_store(body: StoreIn, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    if body.is_open:
        db.set_store_status(True)
        return db.get_store_status()
    back = None
    now = datetime.now(_IST)
    if body.mode == "today":
        back = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    elif body.mode == "hours":
        h = body.hours or 0
        if not 0 < h <= 24 * 7:
            raise HTTPException(400, "Choose how long to close for")
        back = now + timedelta(hours=h)
    reason = (body.reason or "").strip()[:160] or "We're closed for online orders right now."
    db.set_store_status(False, reason=reason, turn_on_time=back.strftime("%Y-%m-%dT%H:%M") if back else None)
    return db.get_store_status()


@app.get("/api/admin/menu-lite")
def admin_menu_lite(x_admin_token: str | None = Header(None)):
    """Everything the kitchen's Menu tab needs: per dish, is it on or sold out today."""
    _check_admin(x_admin_token)
    base = {m["id"]: m for m in menu.base_menu()}
    out = []
    for g in menu.grouped():
        for it in g["items"]:
            if it["id"].endswith(menu.HALF_SUFFIX):
                continue
            b = base.get(it["id"], {})
            full = menu.get_item(it["id"]) or it
            out.append({"id": it["id"], "name": it["name"], "group": g["group"], "price": it["price"],
                        "original": b.get("price", it["price"]),
                        "half": full.get("half_price"), "original_half": b.get("half_price"),
                        "available": it["available"], "custom": bool(it.get("custom")),
                        "reason": it.get("unavailable_reason")})
    return {"items": out, "changes": db.recent_price_changes()}


class PriceIn(BaseModel):
    item_id: str
    price: float
    half_price: float | None = None


def _check_price(v: float) -> float:
    if not 0 < v <= 5000:
        raise HTTPException(400, "Enter a price in rupees")
    return round(v, 2)


@app.post("/api/admin/prices")
def admin_set_price(body: PriceIn, x_admin_token: str | None = Header(None)):
    """Change a dish's price from the kitchen screen. menu.json stays the base;
    the change is stored separately, logged, and can be reset to the base price."""
    _check_admin(x_admin_token)
    base = next((m for m in menu.base_menu() if m["id"] == body.item_id), None)
    if not base:
        raise HTTPException(404, "Unknown dish (specials are changed by adding them again)")
    price = _check_price(body.price)
    half = _check_price(body.half_price) if body.half_price is not None and base.get("half_price") else None
    cur = menu.get_item(body.item_id) or base
    old_price, old_half = cur["price"], cur.get("half_price")
    db.set_price_override(
        body.item_id,
        None if price == base["price"] else price,
        None if half is None or half == base.get("half_price") else half)
    if price != old_price:
        db.log_price_change(body.item_id, base["name"], old_price, price, "price")
    if half is not None and half != old_half:
        db.log_price_change(body.item_id, base["name"], old_half, half, "half")
    if price != old_price or (half is not None and half != old_half):
        _tell_owner_price(base["name"], old_price, price, old_half, half)
    return {"ok": True, "price": price, "half_price": half}


@app.delete("/api/admin/prices/{item_id}")
def admin_reset_price(item_id: str, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    base = next((m for m in menu.base_menu() if m["id"] == item_id), None)
    if not base:
        raise HTTPException(404, "Unknown dish")
    cur = menu.get_item(item_id) or base
    db.set_price_override(item_id, None, None)
    if cur["price"] != base["price"]:
        db.log_price_change(item_id, base["name"], cur["price"], base["price"], "price")
        _tell_owner_price(base["name"], cur["price"], base["price"], None, None)
    return {"ok": True, "price": base["price"]}


def _tell_owner_price(name: str, old: float, new: float, old_half, new_half) -> None:
    """Heads-up to the owner's Telegram so a price change never goes unseen."""
    try:
        from . import telegram
        if telegram.enabled():
            from html import escape
            line = f"Price changed on the kitchen screen: {name} ₹{old:g} → ₹{new:g}"
            if new_half is not None and new_half != old_half:
                line += f" (half ₹{old_half:g} → ₹{new_half:g})"
            telegram.send_message(escape(line))
    except Exception:
        pass


class SoldOutIn(BaseModel):
    item_id: str
    sold_out: bool


@app.post("/api/admin/sold-out")
def admin_sold_out(body: SoldOutIn, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    if menu.is_custom(body.item_id):
        if not db.set_custom_sold_out(body.item_id, body.sold_out):
            raise HTTPException(404, "Unknown dish")
        return {"ok": True}
    base = menu.get_item(body.item_id)
    if not base:
        raise HTTPException(404, "Unknown dish")
    day = menu.today()
    if not db.get_available_ids(day):
        # Nothing saved yet means "everything is on". Save that explicitly first.
        db.set_availability(day, [m["id"] for m in menu.load_menu() if m["id"] != body.item_id], [])
    if body.sold_out:
        db.set_availability(day, [], [body.item_id])
    else:
        db.set_availability(day, [body.item_id], [])
    return {"ok": True}


class CustomItemIn(BaseModel):
    name: str
    price: float
    description: str = ""
    only_today: bool = True


@app.post("/api/admin/custom-items")
def admin_add_custom_item(body: CustomItemIn, x_admin_token: str | None = Header(None)):
    """Add a dish by hand. It lives outside menu.json and the POS catalogue, so
    it can never clash with a POS item; orders containing it are not sent to the POS."""
    _check_admin(x_admin_token)
    name = " ".join(body.name.split())[:60]
    if not name:
        raise HTTPException(400, "Give the dish a name")
    if not 0 < body.price <= 5000:
        raise HTTPException(400, "Enter a price in rupees")
    item_id = menu.custom_item_id(name)
    db.save_custom_item(item_id, name, round(body.price, 2), body.description.strip()[:200],
                        menu.today() if body.only_today else None)
    return {"ok": True, "id": item_id, "name": name}


@app.delete("/api/admin/custom-items/{item_id}")
def admin_remove_custom_item(item_id: str, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    if not db.remove_custom_item(item_id):
        raise HTTPException(404, "Unknown dish")
    return {"ok": True}


# ---- daily specials (admin) ----

class SpecialIn(BaseModel):
    item_name: str
    price: float


@app.get("/api/admin/special")
def admin_get_special(day: str | None = None, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    day = menu.today(day)
    special = db.get_special(day)
    return {"date": day, "special": special}


@app.post("/api/admin/special")
def admin_set_special(body: SpecialIn, day: str | None = None,
                      x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    day = menu.today(day)
    db.set_special(day, body.item_name, body.price)
    return {"date": day, "ok": True, "special": body.model_dump()}


@app.delete("/api/admin/special")
def admin_clear_special(day: str | None = None, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    day = menu.today(day)
    db.clear_special(day)
    return {"date": day, "ok": True}


# ---- reviews (admin) ----

class ReviewIn(BaseModel):
    source: str
    quote: str
    author_name: str | None = None
    rating: int | None = None
    proof_url: str | None = None


class FeaturedIn(BaseModel):
    featured: bool


class PlatformStatsIn(BaseModel):
    platform: str
    rating: float | None = None
    review_count: int | None = None


@app.get("/api/admin/reviews")
def admin_list_reviews(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    return {"reviews": reviews.list_reviews()}


@app.post("/api/admin/reviews")
def admin_add_review(body: ReviewIn, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    try:
        rid = reviews.add_review(body.source, body.quote, author_name=body.author_name,
                                 rating=body.rating, proof_url=body.proof_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": rid}


@app.delete("/api/admin/reviews/{review_id}")
def admin_delete_review(review_id: int, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    reviews.delete_review(review_id)
    return {"ok": True}


@app.post("/api/admin/reviews/{review_id}/feature")
def admin_feature_review(review_id: int, body: FeaturedIn, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    reviews.set_review_featured(review_id, body.featured)
    return {"ok": True, "featured": body.featured}


@app.get("/api/admin/platform-stats")
def admin_get_platform_stats(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    return {"stats": reviews.get_platform_stats()}


@app.post("/api/admin/platform-stats")
def admin_set_platform_stats(body: PlatformStatsIn, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    try:
        reviews.set_platform_stats(body.platform, body.rating, body.review_count)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class OrderCountIn(BaseModel):
    count: int


@app.get("/api/admin/order-count")
def admin_get_order_count(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    return {"count": db.get_order_count_estimate()}


@app.post("/api/admin/order-count")
def admin_set_order_count(body: OrderCountIn, x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    try:
        reviews.set_order_count_estimate(body.count)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---- WhatsApp conversations (admin) ----

class HumanIn(BaseModel):
    human: bool


# ---- admin: order state machine ----

class StatusIn(BaseModel):
    status: str


VALID_TRANSITIONS = {
    "new": ["preparing", "cancelled"],
    "preparing": ["ready", "cancelled"],
    "ready": ["out_for_delivery", "delivered"],  # delivered for pickup
    "out_for_delivery": ["delivered"],
}


@app.post("/api/admin/orders/{order_id}/status")
def admin_update_order_status(order_id: int, body: StatusIn,
                              x_admin_token: str | None = Header(None)):
    """Advance order to next state. Sends WhatsApp notification to customer."""
    _check_admin(x_admin_token)
    o = db.get_order(order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    current = o["status"]
    target = body.status
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise HTTPException(400, f"Cannot move from '{current}' to '{target}'. Allowed: {allowed}")
    db.update_order_status(order_id, target)
    _send_status_whatsapp(o, target)
    if target == "cancelled":
        # Keep the POS in step; its own cancel callback is then a no-op here.
        try:
            from .petpooja.client import cancel_order, is_configured
            if is_configured():
                cancel_order(order_id, "Cancelled from the kitchen screen")
        except Exception:
            log.exception("Petpooja cancel failed for order %s", order_id)
    return {"ok": True, "order_id": order_id, "from": current, "to": target}


@app.post("/api/admin/orders/{order_id}/petpooja-cancel")
def admin_petpooja_cancel(order_id: int, x_admin_token: str | None = Header(None)):
    """Tell Petpooja the order is cancelled (status -1) and return their raw
    response verbatim.

    Doubles as a diagnostic: it makes Petpooja's backend look up the order by
    our clientOrderID, so the answer proves whether the order actually exists
    on their side even when it's invisible on their dashboard (the blank
    orderID + success=1 symptom). Does NOT touch our own order status here —
    that flips when Petpooja's order-callback for the cancellation arrives."""
    _check_admin(x_admin_token)
    o = db.get_order(order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    from .petpooja.client import is_configured, cancel_order
    if not is_configured():
        raise HTTPException(400, "Petpooja not configured")
    try:
        raw = cancel_order(order_id, "Cancelled by admin (Petpooja-connect diagnostic)")
    except Exception as e:
        raise HTTPException(502, f"Petpooja cancel_order failed: {e}")
    return {"order_id": order_id, "ok": str(raw.get("success")) == "1", "response": raw}


def _action_page(heading: str) -> HTMLResponse:
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:70px 24px;"
        f"font-size:20px'>{heading}</body></html>"
    )


@app.get("/order-actions/{order_id}/paid", response_class=HTMLResponse)
def order_action_mark_paid(order_id: int, sig: str):
    """One-tap link from the owner's Telegram order alert — no admin login, just a
    signed link (see app/order_actions.py) since she won't use the admin UI."""
    from .order_actions import verify
    if not verify(order_id, "paid", sig):
        raise HTTPException(403, "Invalid link")
    o = db.get_order(order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    already = o.get("paid")
    db.mark_order_paid(order_id)
    if not already:
        from .notify import notify_payment_received
        notify_payment_received(db.get_order(order_id))
    return _action_page(f"✅ Order #{order_id} marked as paid. The customer has been told.")


@app.get("/order-actions/{order_id}/cancel", response_class=HTMLResponse)
def order_action_cancel(order_id: int, sig: str):
    """One-tap cancel link from the owner's Telegram alert — for orders she cancels
    over a phone call/WhatsApp rather than through Petpooja's own terminal
    (a terminal-side cancel already reaches us via the Petpooja order
    callback and doesn't need this)."""
    from .order_actions import verify
    if not verify(order_id, "cancel", sig):
        raise HTTPException(403, "Invalid link")
    o = db.get_order(order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    if o["status"] in ("delivered", "cancelled"):
        return _action_page(f"Order #{order_id} is already {o['status']} — nothing to cancel.")
    db.update_order_status(order_id, "cancelled")
    _send_status_whatsapp(o, "cancelled")
    if o.get("petpooja_synced_at"):
        try:
            from .petpooja.client import cancel_order as petpooja_cancel_order, is_configured
            if is_configured():
                petpooja_cancel_order(order_id, "Cancelled by restaurant")
        except Exception:
            log.exception("Petpooja cancel relay failed for order %s", order_id)
    return _action_page(f"❌ Order #{order_id} cancelled.")


def _send_status_whatsapp(order: dict, status: str) -> None:
    """Send status update to customer (WhatsApp template if active, else SMS fallback)."""
    from .notify import notify_status
    notify_status(order, status)


@app.get("/api/admin/conversations")
def admin_conversations(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    from .whatsapp import sessions as wa_sessions
    return {"conversations": wa_sessions.all_sessions()}


@app.post("/api/admin/conversations/{wa_id}/human")
def admin_conversation_human(wa_id: str, body: HumanIn,
                             x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    from .whatsapp import sessions as wa_sessions
    wa_sessions.set_human(wa_id, body.human)
    return {"wa_id": wa_id, "human": body.human, "ok": True}


@app.get("/category", response_class=HTMLResponse)
def category_index(request: Request):
    """Index of all food categories — the parent page the 10 category pages
    hang off, with a card (photo, blurb, count, price range) per category."""
    photos = dish_photo_ids()
    groups = []
    for cat in all_categories():
        items = [m for m in menu.load_menu()
                 if m["group"] == cat["name"] and not m["id"].endswith(menu.HALF_SUFFIX)]
        items.sort(key=lambda m: (not m.get("popular"), m["name"]))
        prices = [float(m["price"]) for m in items if isinstance(m.get("price"), (int, float))]
        photo = next((m["id"] for m in items if m["id"] in photos), None)
        groups.append({"name": cat["name"], "slug": cat["slug"], "count": len(items),
                       "blurb": CATEGORY_BLURBS.get(cat["name"]) or "",
                       "photo": photo,
                       "low": min(prices) if prices else None,
                       "high": max(prices) if prices else None})
    index_url = f"{SITE_URL}/category"
    return templates.TemplateResponse(
        request,
        "categories.html",
        {"groups": groups, "categories": all_categories(),
         "total_items": sum(g["count"] for g in groups), "index_url": index_url,
         "index_json_ld": [
             {"@context": "https://schema.org", "@type": "ItemList",
              "name": f"{biz.name} menu categories", "url": index_url,
              "numberOfItems": len(groups),
              "itemListElement": [
                  {"@type": "ListItem", "position": i + 1,
                   "name": g["name"], "url": f"{SITE_URL}/category/{g['slug']}"}
                  for i, g in enumerate(groups)]},
             {"@context": "https://schema.org", "@type": "BreadcrumbList",
              "itemListElement": [
                  {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE_URL},
                  {"@type": "ListItem", "position": 2, "name": "Categories", "item": index_url}]},
         ]},
    )


@app.get("/category/{slug}", response_class=HTMLResponse)
def category_page(request: Request, slug: str):
    """One SEO page per food category, listing every item in it, all linked
    to their individual dish pages and back to /menu."""
    slugs = group_slugs()
    group = next((g for g, s in slugs.items() if s == slug), None)
    if not group:
        raise HTTPException(404, "Category not found")
    items = [m for m in menu.load_menu() if m["group"] == group and not m["id"].endswith(menu.HALF_SUFFIX)]
    photos = dish_photo_ids()
    # Photo items first (no more walls of empty boxes), then popular, then A–Z.
    items.sort(key=lambda m: ((m.get("photo_id") or m["id"]) not in photos,
                              not m.get("popular"), m["name"]))
    items = [dict(m) for m in items]
    day = menu.today()
    for m in items:
        m["available"] = menu.is_available(m["id"], day)
        m["price"] = float(m["price"])
        m["half"] = m.get("half_price")
    prices = [m["price"] for m in items]
    low, high = (min(prices), max(prices)) if prices else (None, None)
    category_url = f"{SITE_URL}/category/{slug}"
    return templates.TemplateResponse(
        request,
        "category.html",
        {"group": group, "slug": slug, "items": items, "categories": all_categories(),
         "count": len(items), "price_low": low, "price_high": high,
         "blurb": CATEGORY_BLURBS.get(group) or "",
         "keywords": seo_aliases.CATEGORY_KEYWORDS.get(group, []),
         "bestsellers": [m for m in items if m.get("popular")][:4],
          "category_url": category_url, "dish_photos": photos,
          "hero": category_hero(slug)},
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Branded 404 so a mistyped slug lands on a page with working links out,
    not a bare not-found line. API callers (/api/...) still get clean JSON."""
    if request.url.path.startswith("/api/"):
        # Re-raising here turned every unknown /api/ path (bots probing
        # /api/.env) into a 500 + traceback; answer the 404 as JSON instead.
        return JSONResponse({"detail": getattr(exc, "detail", "Not Found")}, status_code=404)
    return templates.TemplateResponse(request, "404.html", {"categories": all_categories()}, status_code=404)


@app.get("/404", response_class=HTMLResponse)
def not_found_page(request: Request):
    """The 404 page also lives at a real URL (and in the sitemap) so search
    engines can crawl it and customers can return from a bad slug to the site."""
    return templates.TemplateResponse(request, "404.html", {"categories": all_categories()}, status_code=200)


# ---- orders ----

class OrderItemIn(BaseModel):
    item_id: str
    qty: float = Field(gt=0)
    note: str | None = None


class OrderIn(BaseModel):
    phone: str
    name: str
    address: str | None = None
    order_type: str = "delivery"  # delivery | pickup
    km: float | None = None
    pincode: str | None = None
    lat: str | None = None
    lng: str | None = None
    payment_method: str = "cod"   # cod | upi
    instructions: str | None = None
    scheduled_at: str | None = None
    scheduled_window: str | None = None  # lunch | dinner (else None = asap/custom)
    pay_courier_direct: bool = False      # delivery fee is part of the total (Oct 2026); kept for old clients
    discount_code: str | None = None
    items: list[OrderItemIn]


@app.post("/api/orders")
def create_order(order: OrderIn):
    try:
        result = orders.create_order(
            phone=order.phone, name=order.name, address=order.address,
            order_type=order.order_type, km=order.km, pincode=order.pincode,
            lat=order.lat, lng=order.lng,
            payment_method=order.payment_method, instructions=order.instructions,
            scheduled_at=order.scheduled_at,
            scheduled_window=order.scheduled_window,
            pay_courier_direct=order.pay_courier_direct,
            discount_code=order.discount_code,
            items=[{"item_id": it.item_id, "qty": it.qty, "note": it.note} for it in order.items],
        )
        return result
    except orders.OrderError as e:
        raise HTTPException(e.status, e.message)


@app.get("/api/discount/check")
def discount_check(code: str, subtotal: float, phone: str | None = None):
    """Checkout preview of a discount code. The order itself re-checks it."""
    from . import discounts
    try:
        d = discounts.evaluate(code, orders._normalize_phone(phone) if phone else None, subtotal)
    except discounts.DiscountError as e:
        return {"valid": False, "message": str(e)}
    row = discounts.get(d["code"])
    return {"valid": True, "code": d["code"], "amount": d["amount"], "label": d["label"],
            "kind": row["kind"], "value": row["value"], "max_discount": row["max_discount"],
            "min_subtotal": row["min_subtotal"],
            "message": f"{d['code']} applied: {d['label']}."}


class DiscountCodeIn(BaseModel):
    code: str
    kind: str = "percent"
    value: float
    max_discount: float | None = None
    min_subtotal: float = 0
    once_per_customer: bool = True
    first_order_only: bool = False
    max_uses: int | None = None
    active: bool = True
    expires_at: str | None = None
    note: str | None = None


@app.get("/api/admin/discount-codes")
def admin_discount_codes(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    from . import discounts
    return discounts.list_codes()


@app.post("/api/admin/discount-codes")
def admin_upsert_discount_code(body: DiscountCodeIn, x_admin_token: str | None = Header(None)):
    """Create or update a code. Pause one with {"code": "X", "value": .., "active": false}."""
    _check_admin(x_admin_token)
    from . import discounts
    try:
        return discounts.upsert(body.model_dump())
    except discounts.DiscountError as e:
        raise HTTPException(400, str(e))


@app.get("/api/delivery/quote")
def delivery_quote_api(lat: float, lng: float, subtotal: float = 0.0,
                       address: str | None = None):
    """Live courier quote for a dropped pin (Borzo first, zone fallback)."""
    from .delivery.estimate import estimate
    q = estimate(lat, lng, subtotal, address)
    q["free_enabled"] = FREE_DELIVERY_ENABLED
    return q


@app.get("/api/orders/{ref}")
def get_order(ref: str):
    """Public order lookup — token-first for tracking links; numeric order
    ids accepted for internal/bot callers."""
    o = db.get_order_by_token(ref) if not ref.isdigit() else db.get_order(int(ref))
    if not o:
        raise HTTPException(404, "Order not found")
    o.pop("courier_cost", None)  # what we paid Porter is ours, not the customer's
    o["reviewed_items"] = sorted(db.order_reviewed_items(o["id"]))
    return o


class DishRatingIn(BaseModel):
    item_id: str
    rating: int
    comment: str | None = None


class OrderReviewIn(BaseModel):
    ratings: list[DishRatingIn]
    name: str | None = None


@app.post("/api/orders/{ref}/review")
def review_order(ref: str, body: OrderReviewIn):
    """Dish ratings from the customer, by tracking token only (no numeric
    ids, so nobody can rate someone else's order). Delivered orders only,
    one rating per dish per order: these feed the dish pages' visible
    reviews and their Product aggregateRating / review markup."""
    o = db.get_order_by_token(ref) if not ref.isdigit() else None
    if not o:
        raise HTTPException(404, "Order not found")
    if o["status"] != "delivered":
        raise HTTPException(400, "You can rate your food once it's delivered")
    in_order = {menu._base_id(i["item_id"]) for i in o["items"]}
    first = (body.name or o.get("customer_name") or "").strip().split(" ")[0][:30] or None
    saved = 0
    for r in body.ratings:
        base = menu._base_id(r.item_id)
        if base not in in_order or not 1 <= r.rating <= 5:
            continue
        comment = (r.comment or "").strip()[:500] or None
        saved += db.add_dish_review(o["id"], base, r.rating, comment, first)
    return {"ok": True, "saved": saved}


@app.get("/api/admin/dish-reviews")
def admin_dish_reviews(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    return {"reviews": db.all_dish_reviews()}


class HideIn(BaseModel):
    hidden: bool


@app.post("/api/admin/dish-reviews/{review_id}")
def admin_hide_dish_review(review_id: int, body: HideIn, x_admin_token: str | None = Header(None)):
    """Hide abusive or off-topic reviews (they stay in the database)."""
    _check_admin(x_admin_token)
    db.set_dish_review_hidden(review_id, body.hidden)
    return {"ok": True}


class PhoneLookupIn(BaseModel):
    phone: str


@app.post("/api/orders/lookup")
def lookup_orders_by_phone(body: PhoneLookupIn):
    """For the /track landing page: which orders belong to this phone number?
    Only returns id, tracking token and a couple of safe fields — the full
    order stays behind the token URL."""
    phone = "".join(ch for ch in body.phone if ch.isdigit())[-10:]
    if len(phone) < 10:
        raise HTTPException(422, "Enter a valid 10-digit phone number")
    return {"orders": db.get_orders_by_phone(phone)}


class OrderAddressIn(BaseModel):
    address: str
    pincode: str | None = None
    lat: str | None = None
    lng: str | None = None


@app.post("/api/orders/{order_id}/address")
def edit_order_address(order_id: int, body: OrderAddressIn):
    """Self-service address fix within a few minutes of placing the order."""
    try:
        return orders.edit_address(order_id, body.address, pincode=body.pincode,
                                   lat=body.lat, lng=body.lng)
    except orders.OrderError as e:
        raise HTTPException(e.status, e.message)


@app.get("/api/orders")
def list_orders(limit: int = 20):
    return {"orders": db.recent_orders(limit)}


# ---- admin: today's orders dashboard ----

@app.get("/api/admin/today-orders")
def admin_today_orders(x_admin_token: str | None = Header(None)):
    _check_admin(x_admin_token)
    return {"orders": db.today_orders()}


@app.get("/api/admin/scheduled-orders")
def admin_scheduled_orders(x_admin_token: str | None = Header(None)):
    """All upcoming scheduled orders (any creation date) — the Scheduled
    tab's planning view, since today_orders() only covers today."""
    _check_admin(x_admin_token)
    return {"orders": db.upcoming_scheduled_orders()}


# ---- admin: dispatch (Food Ready) ----

@app.post("/api/admin/orders/{order_id}/dispatch")
def admin_dispatch_order(order_id: int, x_admin_token: str | None = Header(None)):
    """Book a rider: Porter API when configured, else returns the manual booking card."""
    _check_admin(x_admin_token)
    from .orders import dispatch_rider
    try:
        result = dispatch_rider(order_id)
    except orders.OrderError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(500, f"Dispatch failed: {e}")
    if result.get("manual"):
        # Nothing booked yet: the admin UI shows the booking card and posts
        # the Porter link back to /manual-dispatch below.
        return {"ok": True, **result}
    o = db.get_order(order_id)
    _send_dispatch_whatsapp(o, result)
    return {"ok": True, **result}


@app.get("/api/admin/whatsapp/templates")
def admin_whatsapp_templates(x_admin_token: str | None = Header(None)):
    """Compare the templates the code sends with what Meta has approved."""
    _check_admin(x_admin_token)
    from .whatsapp import client, templates
    try:
        waba = client.waba_id()
    except Exception as e:
        raise HTTPException(502, f"Could not find the WhatsApp Business Account: {e}")
    if not waba:
        return {"ok": False, "detail": "Set WHATSAPP_WABA_ID on Railway to check against Meta.",
                "expected": templates.EXPECTED}
    try:
        meta = client.list_templates(waba)
    except Exception as e:
        raise HTTPException(502, f"Could not read templates from Meta: {e}")
    rows = templates.check(meta)
    return {"ok": all(r["ok"] for r in rows), "waba_id": waba, "templates": rows,
            "last_sync": templates.last_sync(),
            "on_meta_but_unused": sorted({t["name"] for t in meta} - set(templates.EXPECTED))}


@app.get("/api/admin/whatsapp/spend")
def admin_whatsapp_spend(days: int = 30, x_admin_token: str | None = Header(None)):
    """What Meta charged for WhatsApp messages, from its pricing analytics."""
    _check_admin(x_admin_token)
    from .whatsapp import client
    try:
        return client.spend(min(max(days, 1), 90))
    except Exception as e:
        raise HTTPException(502, f"Meta pricing analytics failed: {e}")


@app.post("/api/admin/whatsapp/templates/sync")
def admin_whatsapp_templates_sync(x_admin_token: str | None = Header(None)):
    """Create any template the app needs that Meta doesn't have yet."""
    _check_admin(x_admin_token)
    from .whatsapp import templates
    return templates.sync()


class ManualDispatchIn(BaseModel):
    tracking_url: str = ""
    rider_name: str = ""
    rider_phone: str = ""
    fare: float | None = None   # the Porter fare, charged to the customer


@app.post("/api/admin/orders/{order_id}/manual-dispatch")
def admin_manual_dispatch(order_id: int, body: ManualDispatchIn,
                          x_admin_token: str | None = Header(None)):
    """Kitchen booked the rider in the Porter app — record it and tell the customer."""
    _check_admin(x_admin_token)
    try:
        result = orders.record_manual_dispatch(order_id, body.tracking_url,
                                               body.rider_name, body.rider_phone, body.fare)
    except orders.OrderError as e:
        raise HTTPException(e.status, e.message)
    _send_dispatch_whatsapp(db.get_order(order_id), result)
    return {"ok": True, **result}


def _send_dispatch_whatsapp(order: dict, dispatch: dict) -> None:
    """Send tracking notification to customer (WhatsApp template if active, else SMS fallback)."""
    from .notify import notify_dispatch
    notify_dispatch(order, dispatch)


@app.get("/api/orders/{order_id}/track")
def track_order(order_id: int):
    """Get live tracking info for an order."""
    o = db.get_order(order_id)
    if not o:
        raise HTTPException(404, "Order not found")
    if (o.get("sr_courier") or "").startswith("Porter"):
        # Manual Porter booking: the link the owner pasted is the tracking (sr_awb
        # holds the rider's phone there, not an AWB to look up).
        return {"tracking": {"status_text": o["sr_courier"],
                             "tracking_url": o.get("sr_tracking_url") or ""},
                "status": o["status"]}
    if not o.get("sr_awb"):
        return {"tracking": None, "status": o["status"]}

    # Try Borzo tracking first (if it's a Borzo order)
    if o["sr_awb"] and o["sr_awb"].startswith("BZ-"):
        try:
            from .delivery.borzo import get_order as borzo_get_order
            borzo_order = borzo_get_order(o["sr_order_id"])
            return {
                "tracking": {
                    "status_text": borzo_order.get("status", ""),
                    "tracking_url": borzo_order.get("tracking_url", o["sr_tracking_url"]),
                },
                "status": o["status"],
            }
        except Exception:
            pass  # fall through

    # Shiprocket tracking (fallback)
    try:
        from .delivery.shiprocket import track_awb, track_order as sr_track_order
        from .delivery.config import SHIPROCKET_CHANNEL_ID
        if o.get("sr_order_id"):
            try:
                tracking = sr_track_order(str(o["sr_order_id"]), SHIPROCKET_CHANNEL_ID)
                return {"tracking": tracking, "status": o["status"]}
            except Exception:
                pass
        tracking = track_awb(o["sr_awb"])
        return {"tracking": tracking, "status": o["status"]}
    except Exception as e:
        return {"tracking": None, "status": o["status"], "error": str(e)}


@app.on_event("startup")
def on_startup():
    db.init_db()
    db.seeded()
    from . import discounts
    discounts.seed()
    init_qr()
    from .whatsapp import sessions as wa_sessions
    wa_sessions.init_sessions()
    from .config import WHATSAPP_ACTIVE
    if WHATSAPP_ACTIVE:
        # Create any missing WhatsApp templates on Meta, off the boot path.
        import threading
        from .whatsapp import templates as wa_templates
        threading.Thread(target=wa_templates.sync, daemon=True).start()
