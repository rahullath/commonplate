import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.links import extract_tracking_url as x

PORTER = "https://porter.in/t/abc123"


def test_plain():
    assert x(PORTER) == {"state": "ok", "url": PORTER, "host": "porter.in"}


def test_schemeless_and_www():
    assert x("porter.in/t/abc123")["url"] == PORTER
    assert x("www.rapido.bike/track/9")["url"] == "https://www.rapido.bike/track/9"


def test_http_upgraded_and_trailing_punctuation():
    assert x("Track: http://porter.in/t/abc123.")["url"] == PORTER
    assert x("(https://porter.in/t/abc123),")["url"] == PORTER


def test_quotes_and_zero_width():
    assert x("“https://porter.in/t/​abc123”")["url"] == PORTER


def test_two_links_prefers_courier_over_store():
    t = "Get the app https://play.google.com/store/apps/details?id=in.porter.customerapp\nTrack my trip: https://porter.in/t/abc123"
    assert x(t)["url"] == PORTER


def test_store_only():
    assert x("https://play.google.com/store/apps/details?id=x")["state"] == "store"


def test_unknown_host_accepted():
    r = x("https://example.com/trip/1")
    assert r["state"] == "unknown" and r["url"] == "https://example.com/trip/1"


def test_driver_phone_not_a_link():
    assert x("Driver: Ravi, 98765 43210")["state"] == "none"


def test_empty_and_huge():
    assert x("")["state"] == "none"
    assert x("a " * 5000)["state"] == "none"


def test_utm_dropped_only_for_porter():
    assert x("https://porter.in/t/a?utm_source=x&id=7")["url"] == "https://porter.in/t/a?id=7"
    assert x("https://rapido.bike/t/a?utm_source=x")["url"] == "https://rapido.bike/t/a?utm_source=x"
