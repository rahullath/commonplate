"""Turn a typed delivery address into a map point, for the fixed delivery
fee band (the WhatsApp checkout Flow has no map, only an address box).

Google Geocoding first (precise, server key GOOGLE_PLACES_API_KEY), then
OpenStreetMap Nominatim on the full address, then on the pincode alone (a
rough centre point: fine for a 2 km fee band, flagged as approximate).
Returns None when nothing matches; the caller then asks for a location pin.
"""
from .business import business
import logging

import httpx

from .config import GOOGLE_PLACES_API_KEY

log = logging.getLogger("geo")


def geocode(address: str, pincode: str = "") -> dict | None:
    """{'lat', 'lng', 'precise'} or None."""
    query = ", ".join(p for p in (address, pincode, f"{business.city}, {business.state}, {business.country}") if p)
    if GOOGLE_PLACES_API_KEY:
        try:
            r = httpx.get("https://maps.googleapis.com/maps/api/geocode/json", timeout=10,
                          params={"address": query, "key": GOOGLE_PLACES_API_KEY, "region": "in",
                                  "components": "country:IN"})
            data = r.json()
            if data.get("status") == "OK":
                loc = data["results"][0]["geometry"]["location"]
                exact = data["results"][0]["geometry"].get("location_type") != "APPROXIMATE"
                return {"lat": str(loc["lat"]), "lng": str(loc["lng"]), "precise": exact}
            log.info("Google geocode %s for %r", data.get("status"), query[:80])
        except Exception as e:
            log.warning("Google geocode failed: %s", e)
    headers = {"User-Agent": f"{business.name} ordering/1.0 ({business.site_url})"}
    for q, precise in ((query, True), (f"{pincode}, {business.city}, {business.country}" if pincode else "", False)):
        if not q:
            continue
        try:
            r = httpx.get("https://nominatim.openstreetmap.org/search", headers=headers, timeout=10,
                          params={"q": q, "format": "json", "countrycodes": "in", "limit": 1})
            hits = r.json()
            if hits:
                return {"lat": hits[0]["lat"], "lng": hits[0]["lon"], "precise": precise}
        except Exception as e:
            log.warning("Nominatim geocode failed for %r: %s", q[:80], e)
    return None
