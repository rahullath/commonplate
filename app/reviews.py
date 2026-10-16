"""Curated reviews + trust numbers for /about.

Deliberately not a scraper. Google's own rating is pulled through its
official Places API (cached, see refresh_google_rating); other platforms,
Instagram, Facebook, WhatsApp praise and physical reviews are entered by
hand through the admin Reviews tab — there is no ToS-compliant API for
those, and scraping them is exactly the "fabricated/scraped markup" risk
that can get a site penalised by search engines.
"""
import logging
from datetime import datetime, timedelta

import httpx

from . import db
from .config import GOOGLE_PLACES_API_KEY, GOOGLE_PLACE_ID

log = logging.getLogger("reviews")

GOOGLE_RATING_TTL = timedelta(hours=24)

SOURCE_LABELS = {
    "google": "Google",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "whatsapp": "WhatsApp",
    "in_person": "a note at the restaurant",
}


def format_count(n: int) -> str:
    """1100 -> '1.1k+', 2000 -> '2k+', 240 -> '240+'."""
    if n < 1000:
        return f"{n}+"
    value = n / 1000
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text}k+"


def get_order_count_display() -> str | None:
    """The admin-set order count estimate, formatted — or None if unset,
    so the caller can just omit the stat rather than show a fabricated
    default or the (misleadingly tiny) count of web-only orders."""
    n = db.get_order_count_estimate()
    return format_count(n) if n else None


def set_order_count_estimate(count: int) -> None:
    if count < 0:
        raise ValueError("Order count can't be negative")
    db.set_order_count_estimate(count)


def list_reviews() -> list[dict]:
    return db.list_reviews()


def list_featured_reviews(limit: int = 6) -> list[dict]:
    reviews = db.list_featured_reviews(limit)
    for r in reviews:
        r["source_label"] = SOURCE_LABELS.get(r["source"], r["source"])
    return reviews


def add_review(source: str, quote: str, author_name: str | None = None,
               rating: int | None = None, proof_url: str | None = None) -> int:
    if source not in SOURCE_LABELS:
        raise ValueError(f"Unknown source: {source}")
    if not quote or not quote.strip():
        raise ValueError("Quote cannot be empty")
    return db.add_review(source, quote.strip(), author_name=author_name,
                         rating=rating, proof_url=proof_url)


def delete_review(review_id: int) -> None:
    db.delete_review(review_id)


def set_review_featured(review_id: int, featured: bool) -> None:
    db.set_review_featured(review_id, featured)


def set_platform_stats(platform: str, rating: float | None, review_count: int | None) -> None:
    if platform not in ("google",):
        raise ValueError(f"Unknown platform: {platform}")
    db.set_platform_stats(platform, rating, review_count)


def get_platform_stats() -> dict:
    """All platform stats, refreshing Google's row first if it's due (best-effort)."""
    if GOOGLE_PLACES_API_KEY and GOOGLE_PLACE_ID:
        refresh_google_rating()
    return db.get_platform_stats()


def refresh_google_rating() -> None:
    """Pull Google's live rating via Places API (New), at most once every 24h.

    Never raises — a failed or skipped refresh just leaves the last cached
    value in place (or no value, if it's never succeeded yet).
    """
    if not (GOOGLE_PLACES_API_KEY and GOOGLE_PLACE_ID):
        return
    stats = db.get_platform_stats()
    existing = stats.get("google")
    if existing and existing.get("updated_at"):
        try:
            last = datetime.strptime(existing["updated_at"], "%Y-%m-%d %H:%M:%S")
            if datetime.utcnow() - last < GOOGLE_RATING_TTL:
                return  # still fresh, skip the network call
        except ValueError:
            pass
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(
                f"https://places.googleapis.com/v1/places/{GOOGLE_PLACE_ID}",
                headers={
                    "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
                    "X-Goog-FieldMask": "rating,userRatingCount",
                },
            )
            r.raise_for_status()
            data = r.json()
        rating = data.get("rating")
        review_count = data.get("userRatingCount")
        if rating is not None:
            db.set_platform_stats("google", rating, review_count)
    except Exception as e:
        log.warning("Google rating refresh failed: %s", e)
