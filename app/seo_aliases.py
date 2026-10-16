"""Search aliases: the other names people type for the dishes you cook.

Rules for adding aliases:
- Only true synonyms, regional names and common spellings of the SAME dish.
  Never a different dish you do not serve; search engines treat that as a
  mismatch and the page ranks worse.
- Broad intents ("paneer curry", "veg meals") belong at category level in
  CATEGORY_KEYWORDS, not on a dish.

Both tables are empty by default. Add entries here, or load them from
data/aliases.json ({"dishes": {"menu-id": ["alias"]}, "categories": {"Group": ["keyword"]}}).
"""
import json
from pathlib import Path

from .business import business

# Neighbourhoods served, from BIZ_SERVICE_AREAS (comma separated).
NEARBY_AREAS: list[str] = business.service_areas

DISH_ALIASES: dict[str, list[str]] = {}
CATEGORY_KEYWORDS: dict[str, list[str]] = {}

_FILE = Path(__file__).resolve().parent.parent / "data" / "aliases.json"
try:
    _data = json.loads(_FILE.read_text(encoding="utf-8"))
    DISH_ALIASES.update(_data.get("dishes", {}))
    CATEGORY_KEYWORDS.update(_data.get("categories", {}))
except (OSError, ValueError):
    pass


def aliases_for(item_id: str) -> list[str]:
    """Aliases for a menu id (half-portion ids share the full dish's)."""
    return list(DISH_ALIASES.get(item_id.split("__")[0], []))


def item_faqs(name: str, aliases: list[str], price, group: str) -> list[dict]:
    """Dish Q&As, shown on the page and as FAQPage JSON-LD. Answers are facts that
    hold for every dish (where, how much, how to order)."""
    aka = f" (also called {', '.join(aliases[:3])})" if aliases else ""
    where = f"{business.address}"
    return [
        {
            "q": f"Where can I order {name} in {business.city}?",
            "a": (f"{business.name} cooks {name}{aka} fresh to order at {where}. "
                  f"Order online at {business.site_url.split('://', 1)[-1]} for delivery, "
                  "or pick it up from the kitchen."),
        },
        {
            "q": f"How much is {name} at {business.name}?",
            "a": (f"{name} is ₹{price} on the {business.name} menu ({group}). Delivery is a fixed fee "
                  "by distance, shown at checkout before you pay; pickup has no delivery fee."),
        },
    ]
