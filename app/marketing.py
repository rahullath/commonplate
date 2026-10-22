"""Content for the /commonplate page: what the project is and who it is for.

All figures on the page are sample data and the page says so. Kept here, not in
the template, so the interactive demos and the crawlable text share one source.
"""
import os

SITE_REPO = "https://github.com/rahullath/commonplate"
# Both optional. Set them to show a "live kitchen" link and a contact address on the project
# page and guide; left empty, those links are not rendered.
DEMO_URL = os.environ.get("COMMONPLATE_DEMO_URL", "").strip().rstrip("/")
CONTACT_EMAIL = os.environ.get("COMMONPLATE_CONTACT_EMAIL", "").strip()

KLINES = {
    "app": [
        {"k": "Food total", "v": "₹400", "c": "#17120D"},
        {"k": "App commission, 30% (example)", "v": "−₹120", "c": "#C8321E"},
        {"k": "Payment and ad fees", "v": "−₹8", "c": "#C8321E"},
        {"k": "Customer's number", "v": "the app keeps it", "c": "#C8321E"},
    ],
    "direct": [
        {"k": "Food total", "v": "₹400", "c": "#17120D"},
        {"k": "Commission", "v": "₹0", "c": "#1F3FA8"},
        {"k": "UPI to your account", "v": "₹0 fee", "c": "#1F3FA8"},
        {"k": "Customer's number", "v": "yours", "c": "#1F3FA8"},
    ],
}
KEEP = {"app": {"v": "₹272", "c": "#C8321E"}, "direct": {"v": "₹400", "c": "#1F3FA8"}}
KNOTES = {
    "app": "Sample figures. Commission rates vary by app and contract.",
    "direct": "Sample figures. A flat monthly fee covers hosting; the kitchen pays the same however many orders come in.",
}

BALLOT = {"Riders": [14, 9], "Kitchen staff": [6, 2], "Kitchens": [3, 2]}
_COLORS = {"Riders": "#C8321E", "Kitchen staff": "#1F3FA8", "Kitchens": "#17120D"}
CLASSES = [{"name": n, "yes": y, "no": no, "color": _COLORS[n], "pct": round(100 * y / (y + no))}
           for n, (y, no) in BALLOT.items()]
WEIGHTED = round(sum(c["pct"] for c in CLASSES) / len(CLASSES))


def _core(tag, name, body):
    works = tag == "now"
    return {"name": name, "body": body,
            "tag": "WORKS TODAY" if works else "TO BUILD",
            "color": "#1F3FA8" if works else "#C8321E",
            "border": "2px solid #1F3FA8" if works else "2px dashed #C8321E",
            "rot": -1.5 if works else 1.5}


CORE = [
    _core("now", "Direct ordering", "Menu, cart, UPI or cash checkout, order tracking and WhatsApp updates."),
    _core("now", "Kitchen screen", "One screen for incoming orders, built for a busy counter."),
    _core("now", "Courier router", "Borzo, Porter and Shiprocket adapters, delivery zones and fee estimates."),
    _core("now", "POS import", "A Petpooja reader that brings a menu across. The pattern for other importers."),
    _core("next", "Rider pool and receipts", "Offers with reasons, trip receipts, work record export."),
    _core("next", "Rules and votes", "Rules in a file, proposals, secret ballots, public history, plus the fund ledger."),
]

STEPS = [
    {"n": "1", "mt": 0, "bg": "#C8321E", "fg": "#EFE6D2", "title": "Sell direct", "body": "A kitchen runs its own page and stops paying commission."},
    {"n": "2", "mt": 36, "bg": "#F0B21B", "fg": "#17120D", "title": "Pool the riders", "body": "Several kitchens share riders, with receipts and posted rates."},
    {"n": "3", "mt": 72, "bg": "#1F3FA8", "fg": "#EFE6D2", "title": "Make it official", "body": "Register the co-op, enrol riders in welfare schemes, buy group cover."},
    {"n": "4", "mt": 108, "bg": "#EFE6D2", "fg": "#17120D", "title": "Members decide", "body": "Riders, staff and kitchens vote on fees, pay and shift limits."},
]

ROLES = ["Tech", "Product", "Finance", "Organising"]
SKILLS = [
    [{"title": "Full-stack, shipped", "body": "Python, FastAPI, Jinja and SQLite behind a live ordering site. One process, no build step, AGPL licensed so it stays open."},
     {"title": "Integrations", "body": "WhatsApp Cloud API, Petpooja POS, three courier APIs, UPI, Telegram. Each one off until configured."},
     {"title": "Design to code", "body": "Mockups, screens and the working app come from the same hands, so nothing is lost in handover."}],
    [{"title": "Product from a real operator", "body": "The base grew out of a kitchen that is live and taking orders, with fixes driven by actual use."},
     {"title": "Edge cases written down", "body": "Network errors, order minimums, multi-order kitchens, discounts with limits. Documented before built."},
     {"title": "Staged scope", "body": "Three audiences, one core, and a rule for when to expand. Not everything at once."}],
    [{"title": "Unit economics", "body": "Commission versus flat fee, per order and per month. A break-even count of kitchens for a co-op to cover its costs."},
     {"title": "Pricing and tax", "body": "Delivery fare formulas, discount rules with caps, and GST or VAT handling kept in config."},
     {"title": "Ledger design", "body": "A fund with a visible balance and approved payouts, so members can audit it without asking."}],
    [{"title": "Worker-facing design", "body": "Receipts, due process, grievance routes and secret ballots designed for people on low-end phones."},
     {"title": "Governance as software", "body": "Rules in a file, each with the group allowed to change it. Changes are public."},
     {"title": "A path, not a pitch", "body": "Direct selling, then a rider pool, then registration and insurance, then member votes."}],
]
ROTS = [-1.8, 1.2, -0.8]
TAPES = [-5, 4, -3]


def page_context(site_url: str, canonical_path: str = "/commonplate") -> dict:
    return {
        "canonical": f"{site_url}{canonical_path}", "demo_url": DEMO_URL,
        "contact_email": CONTACT_EMAIL, "repo": SITE_REPO,
        "klines": KLINES, "keep": KEEP, "knotes": KNOTES, "classes": CLASSES, "weighted": WEIGHTED,
        "ballot": BALLOT, "core": CORE, "steps": STEPS, "roles": ROLES, "skills": SKILLS,
        "rots": ROTS, "tapes": TAPES,
        "ld": {
            "@context": "https://schema.org",
            "@type": "SoftwareSourceCode",
            "name": "commonplate",
            "description": "Open-source direct ordering for small kitchens, with transparent rider pay and worker votes.",
            "codeRepository": SITE_REPO,
            "url": f"{site_url}{canonical_path}",
            "programmingLanguage": "Python",
            "runtimePlatform": "FastAPI, Jinja2, SQLite",
            "license": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
    }
