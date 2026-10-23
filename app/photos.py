"""Dish photos added from the kitchen screen.

They live on the data volume (so a deploy never removes them) and are served from the same
URL the repo's own photos use, /static/img/dishes/<id>.jpg, with an uploaded photo winning over
a bundled one. The browser shrinks the picture before it is sent; this only checks it is a
reasonable JPEG and stores it.
"""
import re
from pathlib import Path

from .config import DATA_DIR

UPLOAD_DIR = DATA_DIR / "photos"
BUNDLED_DIR = Path("app/static/img/dishes")
MAX_BYTES = 3 * 1024 * 1024
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")


def valid_id(item_id: str) -> bool:
    return bool(_NAME.match(item_id))


def uploaded_ids() -> set[str]:
    if not UPLOAD_DIR.is_dir():
        return set()
    return {p.stem for p in UPLOAD_DIR.glob("*.jpg")}


def path_for(item_id: str) -> Path | None:
    """The file to serve for a dish id: an uploaded photo first, then a bundled one."""
    if not valid_id(item_id):
        return None
    for d in (UPLOAD_DIR, BUNDLED_DIR):
        p = d / f"{item_id}.jpg"
        if p.is_file():
            return p
    return None


def version(item_id: str) -> int:
    """Changes whenever the photo does, so the screen can bypass its cache."""
    p = UPLOAD_DIR / f"{item_id}.jpg"
    try:
        return int(p.stat().st_mtime)
    except OSError:
        return 0


def save(item_id: str, data: bytes) -> None:
    if not valid_id(item_id):
        raise ValueError("Bad dish id")
    if len(data) < 1024:
        raise ValueError("That picture is empty")
    if len(data) > MAX_BYTES:
        raise ValueError("That picture is too big")
    if data[:3] != b"\xff\xd8\xff":
        raise ValueError("Please choose a photo (JPEG)")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = UPLOAD_DIR / f".{item_id}.tmp"
    tmp.write_bytes(data)
    tmp.replace(UPLOAD_DIR / f"{item_id}.jpg")


def remove(item_id: str) -> bool:
    p = UPLOAD_DIR / f"{item_id}.jpg"
    if valid_id(item_id) and p.is_file():
        p.unlink()
        return True
    return False
