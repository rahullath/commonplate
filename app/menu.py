"""Menu catalogue loaded from data/menu.json + per-day availability.

Half-portion items: any item with a `half_price` set (see
scripts/merge_petpooja.py) gets a synthetic second entry with id
`<item_id>__half`, name suffixed " (Half)", priced at half_price. It shares
the base item's availability/day-gating — no separate admin toggle.

Specialities-day gating: the "Specialities" group (kachoris, chutneys, papad
packs) is only orderable on Thursdays per how the restaurant runs it —
enforced in is_available() so it also blocks checkout, not just display.
"""
import json
from datetime import date
from functools import lru_cache

from .config import MENU_FILE
from . import db

HALF_SUFFIX = "__half"

SPECIALITIES_GROUP = "Specialities"
SPECIALITIES_WEEKDAY = 3  # Thursday (Monday = 0)


@lru_cache(maxsize=1)
def load_menu() -> list[dict]:
    data = json.loads(MENU_FILE.read_text(encoding="utf-8"))
    items = data["items"]
    by_id = {m["id"]: m for m in items}
    return items


def _base_id(item_id: str) -> str:
    return item_id[: -len(HALF_SUFFIX)] if item_id.endswith(HALF_SUFFIX) else item_id


def get_item(item_id: str) -> dict | None:
    if item_id.endswith(HALF_SUFFIX):
        base = get_item(_base_id(item_id))
        if not base or not base.get("half_price"):
            return None
        half = dict(base)
        half["id"] = item_id
        half["name"] = f"{base['name']} (Half)"
        half["price"] = base["half_price"]
        half["popular"] = False
        return half
    for m in load_menu():
        if m["id"] == item_id:
            return m
    return None


def _specialities_reason(base_item: dict | None, day: str) -> str | None:
    if not base_item or base_item.get("group") != SPECIALITIES_GROUP:
        return None
    if date.fromisoformat(day).weekday() != SPECIALITIES_WEEKDAY:
        return "Only available Thursdays"
    return None


def is_available(item_id: str, day: str | None = None) -> bool:
    day = today(day)
    base_id = _base_id(item_id)
    base_item = get_item(base_id)
    if _specialities_reason(base_item, day):
        return False
    if base_id in _off_pos():
        return False
    available = db.get_available_ids(day)
    if not available:
        return True  # no availability saved yet → everything available
    return base_id in available


def _off_pos() -> frozenset:
    """Dishes the Petpooja POS can't receive — not orderable online, since
    an order line the terminal doesn't know is exactly what we must never
    send (see app/petpooja/mapping.py). Add them on the POS to re-enable."""
    try:
        from .petpooja.catalog import off_pos_ids
        return off_pos_ids()
    except Exception:
        return frozenset()


def today(day: str | None = None) -> str:
    return day or date.today().isoformat()


def menu_for(day: str | None = None) -> list[dict]:
    """Full menu with today's availability flag, half-portion variants included."""
    day = today(day)
    available = db.get_available_ids(day)
    off_pos = _off_pos()
    out = []
    for m in load_menu():
        row = dict(m)
        in_stock = m["id"] in available if available else True
        reason = _specialities_reason(m, day)
        row["available"] = in_stock and not reason and _base_id(m["id"]) not in off_pos
        row["unavailable_reason"] = reason
        row["photo_id"] = m["id"]
        out.append(row)
        if m.get("half_price"):
            half = dict(m)
            half["id"] = m["id"] + HALF_SUFFIX
            half["name"] = f"{m['name']} (Half)"
            half["price"] = m["half_price"]
            half["popular"] = False
            half["available"] = row["available"]
            half["unavailable_reason"] = row["unavailable_reason"]
            half["photo_id"] = m["id"]  # reuse the full-portion photo
            out.append(half)
    return out


def grouped(day: str | None = None) -> list[dict]:
    """Menu grouped by display group, available-first within each group."""
    items = menu_for(day)
    groups: dict[str, list[dict]] = {}
    order = []
    for m in items:
        g = m["group"]
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(m)
    for g in order:
        groups[g].sort(key=lambda x: (not x["available"], not x["popular"], x["name"]))
    return [{"group": g, "items": groups[g]} for g in order]
