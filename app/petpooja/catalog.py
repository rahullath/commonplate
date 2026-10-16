"""Reconcile our menu item slugs (data/menu.json) to Petpooja's real catalog
item ids — the fix for KNOWN GAP §3.2 documented in app/petpooja/mapping.py.

Source of truth: DATA_DIR/petpooja_menu_raw.json, written whenever the
inbound Push Menu webhook (app/webhooks.py, petpooja_push_menu) gets a fresh
push from the Petpooja POS/dashboard ("Menu Trigger", restID 84713). Each
item in that payload carries its real `itemid` (a string), and the addon
groups carry `addongroupid`/`addonitemid`.

Before this module, Save Order sent our own slug as OrderItem.id (e.g.
"north-indian-thali"). Petpooja accepts the call (success=1) but the item id
doesn't exist in the catalog, so the order was invisible / mis-attributed on
the POS and the Online Orders dashboard. This module resolves each slug to
the real catalog id by matching *our* item name (from data/menu.json) against
the pushed `itemname`, then falls back to the slug with a loud log line so a
missing menu item never silently breaks an order.

Notes on matching:
- Match key is the normalized name only (lowercased, non-alphanumerics
  stripped): "Chola Bhatura" in both sources hits. Price is deliberately NOT
  part of the key — menu.json prices are marketplace averages, not the
  Petpooja catalogue's.
- Half portions (<id>__half) resolve to the base item's itemid when the POS
  has no separate " (Half)" entry — the receipt still shows the "(Half)"
  name and half price, both sent through in the Save Order payload.
- SLUG_ALIASES maps the handful of slugs whose POS name is a differently-
  spelled match that normalised equality can't catch (e.g.
  "mutter-paneer" → catalog "Matter Paneer" itemid 1250363084). Items
  genuinely absent from the catalogue stay deliberately unlisted, so they
  degrade to the slug fallback + a logged warning instead of sending a
  fake id.
- Tax: the catalogue marks its items `tax_inclusive: true` and the pushed
  tax rates are CGST 2.5% + SGST 2.5% = 5%, matching our GST_RATE. The Save
  Order payload's per-item `tax_inclusive` now mirrors the catalogue's own
  flag (see map_order_items) instead of hardcoding False, so the POS doesn't
  re-materialize GST on top of the exact total the customer already paid.
  The item price we send stays our checkout price and the CGST/SGST lines
  still reflect the GST the customer actually paid — flag mirrored, money
  unchanged. (True dollar-for-euro alignment would mean sourcing item prices
  from the catalogue too — a menu-sync follow-up, since data/menu.json
  prices are flagged as "approximate platform averages".)
"""
import json
import logging
import re
import sys

from ..config import DATA_DIR
from ..menu import HALF_SUFFIX, _base_id, get_item

log = logging.getLogger("petpooja.catalog")

CACHE_FILE = DATA_DIR / "petpooja_menu_raw.json"
# Production mounts a volume over /app/data, so the committed catalogue never
# reaches the running container; the Dockerfile ships it here as a fallback.
# Without it a fresh volume (no Menu Push yet) sent every order out with
# made-up item ids.
BUNDLED_FILE = DATA_DIR.parent / "petpooja_menu_raw.json.bundled"


def _source_file():
    """The pushed catalogue on the volume if present, else the bundled copy."""
    for f in (CACHE_FILE, BUNDLED_FILE):
        try:
            if f.stat().st_size > 2:
                return f
        except OSError:
            continue
    return None

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(name: str) -> str:
    return _NON_ALNUM.sub("", (name or "").lower())


# Curated overrides for the handful of dishes whose POS catalogue name isn't
# a normalized match of ours (typos/aliases that name-matching can't fix):
#   chilli-cheeesetoast  -> "Chilli Cheese Toast Sandwich" (our "Cheeesetoast" typo)
#   mutter-paneer        -> "Matter Paneer" (different spelling on the POS)
#   vegetable-hakka-noodles -> "Veg Hakka Noodles"
# Keyed by our data/menu.json item slug, values are the real `itemid` from
# the pushed catalogue. Anything genuinely absent from the POS catalogue
# (onion-pakoda, raita-250ml) deliberately stays unlisted so it degrades to
# the slug fallback + warning instead of sending a made-up id.
SLUG_ALIASES: dict[str, str] = {
    "chilli-cheeesetoast": "1250363047",
    "mutter-paneer": "1250363084",
    "vegetable-hakka-noodles": "1250363102",
}


class Catalog:
    """Snapshot of the pushed menu, built once per request.

    Re-reading a ~tens-of-kB file per order is cheap and keeps every order
    using the latest push, so no long-lived cache is kept.
    """

    def __init__(self, raw: dict, aliases: dict[str, str] | None = None):
        self._aliases = aliases or SLUG_ALIASES
        self._by_name: dict[str, dict] = {}
        self._by_itemid: dict[str, dict] = {}
        self._addon_by_name: dict[str, str] = {}
        for it in raw.get("items") or []:
            iid = str(it.get("itemid") or "")
            if iid:
                self._by_itemid[iid] = it
            n = _norm(it.get("itemname", ""))
            if not n:
                continue
            existing = self._by_name.get(n)
            # Prefer the entry that's active over a same-named inactive one
            # (e.g. a catalog keeping both an old and new item under one name).
            if existing is None or (existing.get("active") != 1 and it.get("active") == 1):
                self._by_name[n] = it
        for group in raw.get("addongroups") or []:
            for a in group.get("addongroupitems") or []:
                an = _norm(a.get("addonitem_name", ""))
                if an and an not in self._addon_by_name:
                    self._addon_by_name[an] = str(a.get("addonitemid") or "")

    def item_by_name(self, name: str) -> dict | None:
        return self._by_name.get(_norm(name))

    def addon_item_id(self, addon_name: str) -> str | None:
        return self._addon_by_name.get(_norm(addon_name))

    def item_tax_inclusive(self, name: str) -> bool:
        rec = self.item_by_name(name)
        return bool(rec and rec.get("tax_inclusive"))

    def _match(self, menu_item_id: str) -> tuple[dict | None, dict | None]:
        """Return the (catalog record, menu item) for a slug.

        Falls back to the base item for "<id>__half" slugs (the POS has no
        separate " (Half)" entry — half portions share the base item's id,
        name flag, and tax treatment). (None, None) when the slug is unknown.
        """
        m = get_item(menu_item_id)
        if not m:
            return None, None
        # Curated slug->itemid override first (name-matching can't reach a
        # differently-spelled catalogue name). Half slugs alias via base.
        alias_key = _base_id(menu_item_id) if menu_item_id.endswith(HALF_SUFFIX) else menu_item_id
        target = self._aliases.get(alias_key)
        if target:
            rec = self._by_itemid.get(str(target))
            if rec is not None:
                return rec, m
        rec = self.item_by_name(m["name"])
        if rec is not None:
            return rec, m
        if menu_item_id.endswith(HALF_SUFFIX):
            base = get_item(_base_id(menu_item_id))
            if base:
                rec = self.item_by_name(base["name"])
                if rec is not None:
                    return rec, m
        return None, m

    def resolve(self, menu_item_id: str) -> str:
        """Map a data/menu.json slug to Petpooja's real `itemid`.

        Returns the slug unchanged when unmappable so callers keep working
        (with a logged warning) instead of dropping the line.
        """
        rec, m = self._match(menu_item_id)
        if rec is None:
            if m is not None:
                log.warning("Petpooja catalog: no itemid for '%s' (%s); falling back to slug",
                            m["name"], menu_item_id)
            return menu_item_id
        if rec.get("active") == 0:
            log.warning("Petpooja catalog: '%s' (%s) maps to inactive itemid %s",
                        m["name"], menu_item_id, rec.get("itemid"))
        return str(rec.get("itemid") or menu_item_id)


def _catalog() -> "Catalog | None":
    try:
        src = _source_file()
        if src is None:
            return None
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.exception("Petpooja catalog: could not read the catalogue")
        return None
    return Catalog(raw)


# Production outlet's tax ids (from the pushed menu's `taxes` block, Sep 2026)
# — used if the cache file is missing so we never fall back to made-up ids.
_DEFAULT_TAX_IDS = {"CGST": "128315", "SGST": "128316"}


def tax_ids() -> dict[str, str]:
    """{"CGST": taxid, "SGST": taxid} from the POS catalogue's own tax table.

    Save Order's item_tax/Tax.details ids must be these real ids: the
    catalogue declares every item as `item_tax: "128315,128316"`, and an id
    the POS can't resolve is the leading suspect for the terminal app
    crashing on every incoming order (see docs/PETPOOJA_INTEGRATION.md §3.9).
    """
    ids = dict(_DEFAULT_TAX_IDS)
    try:
        src = _source_file()
        if src is None:
            return ids
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ids
    for t in raw.get("taxes") or []:
        name = str(t.get("taxname", "")).strip().upper()
        if name in ids and t.get("taxid") and str(t.get("active", "1")) == "1":
            ids[name] = str(t["taxid"])
    return ids


_off_pos_cache: dict = {"mtime": None, "ids": frozenset()}


def off_pos_ids() -> frozenset:
    """Menu ids (base, no __half) the POS catalogue doesn't have, cached on
    the cache file's mtime. Empty when Petpooja isn't configured or there's
    no pushed catalogue, so local dev / a fresh deploy hides nothing."""
    from .client import is_configured
    if not is_configured():
        return frozenset()
    src = _source_file()
    if src is None:
        return frozenset()
    mtime = (str(src), src.stat().st_mtime)
    if _off_pos_cache["mtime"] != mtime:
        from .. import menu as menu_mod
        c = _catalog()
        ids = set()
        if c is not None and c._by_name:
            for m in menu_mod.load_menu():
                if not m["id"].endswith(HALF_SUFFIX) and c._match(m["id"])[0] is None:
                    ids.add(m["id"])
        _off_pos_cache.update(mtime=mtime, ids=frozenset(ids))
    return _off_pos_cache["ids"]


def is_on_pos(menu_item_id: str) -> bool:
    """True when the dish resolves to a real POS catalogue item. No cache
    file (local dev / Petpooja off) counts as True so nothing gets hidden."""
    c = _catalog()
    if c is None or not c._by_name:
        return True
    rec, _ = c._match(menu_item_id)
    return rec is not None


def petpooja_item_id(menu_item_id: str) -> str:
    """1-item convenience wrapper around Catalog.resolve()."""
    c = _catalog()
    return c.resolve(menu_item_id) if c else menu_item_id


def map_order_items(order_items: list[dict]) -> list[dict]:
    """Annotate each order item with ``petpooja_item_id`` (real catalog id)
    and ``petpooja_tax_inclusive`` (the catalog's own tax flag for that item).

    ``order_items`` follows app.db.get_order()'s shape ({item_id, name,
    price, qty}); copies get the new keys alongside the original slug.
    Unmapped items keep the slug and default to tax_exclusive — same
    behaviour as before §3.2 landed, so a missing catalog item can't
    silently change how an order is taxed.
    """
    c = _catalog()
    out = []
    for it in order_items:
        o = dict(it)
        rec, _ = c._match(o["item_id"]) if c else (None, None)
        o["petpooja_item_id"] = str(rec.get("itemid") or o["item_id"]) if rec else str(o["item_id"])
        o["petpooja_tax_inclusive"] = bool(rec and rec.get("tax_inclusive"))
        o["petpooja_name"] = (rec.get("itemname") or "") if rec else ""
        out.append(o)
    return out


# ---------------------------------------------------------------- reporting

def _coverage_report() -> None:
    """Compare every data/menu.json item against the pushed catalogue.

    Prints matched/unmatched counts and lists so we can spot menu.json items
    Petpooja's catalogue doesn't know (to prune or to fix on the POS). No
    cache file yet prints a plain hint. Exits non-zero on any miss.
    """
    from .. import menu as menu_mod

    items = [m for m in menu_mod.load_menu() if not m["id"].endswith(HALF_SUFFIX)]
    c = _catalog()
    if c is None:
        print(f"no cache file at {CACHE_FILE} — trigger Menu Push from the POS "
              "dashboard first (or copy the pushed payload here).")
        sys.exit(2)
    if not c._by_name:
        print(f"{CACHE_FILE} has no items ({CACHE_FILE.stat().st_size} bytes) — "
              "this is likely the stale placeholder; restore the real pushed "
              "payload before running the coverage check.")
        sys.exit(2)

    matched, unmatched = [], []
    for m in items:
        rec, _ = c._match(m["id"])
        if rec is not None:
            matched.append(m)
        else:
            unmatched.append(m)
    print(f"menu.json sellable items: {len(items)}")
    print(f"  matched in Petpooja catalogue: {len(matched)}")
    print(f"  NOT found: {len(unmatched)}")
    for m in sorted(unmatched, key=lambda x: x["name"]):
        print(f"  - {m['name']}  ({m['id']})")
    if unmatched:
        print("(resolve each of these by adding it to the POS catalogue or pruning menu.json)")
    sys.exit(1 if unmatched else 0)


def _self_test() -> None:
    """Sanity-check resolution against a synthetic sample of the pushed shape."""
    sample = {
        "items": [
            {"itemid": "1250363094", "itemname": "North Indian Thali",
             "itemallowaddon": "N", "active": 1, "tax_inclusive": True},
            {"itemid": "1250363047", "itemname": "Chilli Cheese Toast Sandwich",
             "active": 1, "tax_inclusive": True},
            {"itemid": "1277216995", "itemname": "Rajasthani Thali",
             "active": 0, "tax_inclusive": True},
            {"itemid": "1250363095", "itemname": "Mini Thali",
             "addon": [{"addon_group_id": "553159"}], "active": 1},
            {"itemid": "666", "itemname": "Chola Bhatura (Full)", "active": 1},
        ],
        "addongroups": [
            {"addongroupid": "553159", "addongroupname": "Add Ons", "addongroupitems": [
                {"addonitemid": "13652618", "addonitem_name": "Pepsi (300 Ml)",
                 "addonitem_price": 70}]},
        ],
    }
    c = Catalog(sample, aliases={"chilli-cheeesetoast": "1250363047"})
    assert c.resolve("north-indian-thali") == "1250363094"
    assert c.resolve("chilli-cheeesetoast") == "1250363047"
    assert c.resolve("rajasthani-thali") == "1277216995"
    assert c.addon_item_id("Pepsi (300 Ml)") == "13652618"
    assert c.item_tax_inclusive("North Indian Thali") is True
    assert c.resolve("no-such-slug") == "no-such-slug"
    assert c.resolve("half") == "half"  # get_item("half") is None
    mod = sys.modules[__name__]
    orig = mod._catalog
    mod._catalog = lambda: c
    try:
        mapped = mod.map_order_items([{"item_id": "north-indian-thali",
                                       "name": "North Indian Thali",
                                       "price": 248, "qty": 1},
                                      {"item_id": "totally-unknown-dish",
                                       "name": "totally-unknown-dish",
                                       "price": 10, "qty": 1}])
    finally:
        mod._catalog = orig
    assert mapped[0]["petpooja_item_id"] == "1250363094"
    assert mapped[0]["petpooja_tax_inclusive"] is True
    assert mapped[1]["petpooja_item_id"] == "totally-unknown-dish"
    assert mapped[1]["petpooja_tax_inclusive"] is False
    print("catalog self-test OK")
    sys.exit(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if "--self-test" in sys.argv:
        _self_test()
    else:
        _coverage_report()