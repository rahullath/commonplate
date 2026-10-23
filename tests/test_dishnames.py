import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dishnames as d  # noqa: E402

MENU = ["Paneer Butter Masala", "Aloo Sabzi", "Masala Chai", "Veg Biryani"]


def fixed(text):
    c = d.clean(text)
    return d.apply(c, d.suggestions(c, MENU))


def test_clean_spacing_and_case():
    assert d.clean("  mango   LASSI ") == "Mango Lassi"
    assert d.clean("veg roll (half)") == "Veg Roll (Half)"
    assert d.clean("joe's special thali") == "Joe's Special Thali"


def test_common_slips_are_suggested():
    assert fixed("briyani special") == "Biryani Special"
    assert fixed("cheeshy disk") == "Cheesy Disc"
    assert fixed("paneer butter masla") == "Paneer Butter Masala"


def test_fine_spellings_are_left_alone():
    assert fixed("sabji sabzi") == "Sabji Sabzi"
    assert d.suggestions("Mango Lassi", MENU) == []


def test_key_ignores_case_and_spacing():
    assert d.key("Aloo  Sabzi") == d.key("aloo sabzi")
