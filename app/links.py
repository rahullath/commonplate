"""Pull the rider's tracking link out of whatever Porter/Rapido's Share put on
the clipboard. Mirrored in static/kitchen.js (cleanLink): keep both in step."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_ZERO_WIDTH = re.compile("[​-‍﻿]")
_QUOTES = re.compile("[“”‘’]")
_CANDIDATE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'()\[\]]+"
    r"|\b(?:[a-z0-9-]+\.)+(?:in|com|bike|app|co)/[^\s<>\"'()\[\]]+", re.I)
_STORE = re.compile(r"(^|\.)(play\.google\.com|apps\.apple\.com|itunes\.apple\.com)$")
_GOOD = re.compile(r"(^|\.)(porter\.in|rapido\.bike|rapido\.in|borzodelivery\.com)$")
_MAX_LEN = 5000


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _tidy(url: str) -> str:
    url = url.rstrip(".,;:!?)]")
    if re.match(r"http://", url, re.I):
        url = "https://" + url[7:]
    elif not re.match(r"https://", url, re.I):
        url = "https://" + url
    if re.search(r"(^|\.)porter\.in$", _host(url)):
        p = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
        url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    return url


def extract_tracking_url(text: str) -> dict:
    """-> {state, url, host}. state: ok (known courier host), unknown (some other
    link: accept, show "check it"), store (only app-store links), none."""
    norm = _ZERO_WIDTH.sub("", text or "")[:_MAX_LEN]
    norm = re.sub(r"\s*\n\s*", " ", _QUOTES.sub('"', norm))
    found = [_tidy(u) for u in _CANDIDATE.findall(norm)]
    real = [u for u in found if _host(u) and not _STORE.search(_host(u))]
    best = next((u for u in real if _GOOD.search(_host(u))), None)
    if best:
        return {"state": "ok", "url": best, "host": _host(best)}
    if real:
        return {"state": "unknown", "url": real[0], "host": _host(real[0])}
    return {"state": "store" if found else "none", "url": "", "host": ""}
