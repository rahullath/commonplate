"""SQLite persistence: customers, orders, order_items, availability.

Availability is keyed by (date, item_id) with available=1/0.
"Repeat yesterday" copies the most recent saved day.
"""
import json
import sqlite3
from datetime import date
from pathlib import Path

from .config import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT UNIQUE,
    name       TEXT,
    address    TEXT,
    pincode    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER REFERENCES customers(id),
    status          TEXT NOT NULL DEFAULT 'new',
    order_type      TEXT NOT NULL DEFAULT 'delivery',
    subtotal        REAL NOT NULL,
    delivery_fee    REAL NOT NULL DEFAULT 0,
    packing_fee     REAL NOT NULL DEFAULT 0,
    gst_amount      REAL NOT NULL DEFAULT 0,
    total           REAL NOT NULL,
    payment_method  TEXT NOT NULL DEFAULT 'cod',
    paid            INTEGER NOT NULL DEFAULT 0,
    instructions    TEXT,
    scheduled_at    TEXT,
    delivery_address TEXT,
    delivery_pincode TEXT,
    delivery_lat    TEXT,
    delivery_lng    TEXT,
    address_flagged INTEGER NOT NULL DEFAULT 0,
    address_flag_reason TEXT,
    sr_order_id     INTEGER,
    sr_awb          TEXT,
    sr_courier      TEXT,
    sr_tracking_url TEXT,
    dispatched_at   TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS customer_addresses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    label       TEXT,
    address     TEXT NOT NULL,
    landmark    TEXT,
    lat         TEXT,
    lng         TEXT,
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS order_items (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES orders(id),
    item_id TEXT,
    name    TEXT,
    price   REAL,
    qty     REAL
);
CREATE TABLE IF NOT EXISTS availability (
    date      TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (date, item_id)
);
CREATE TABLE IF NOT EXISTS daily_specials (
    date      TEXT PRIMARY KEY,
    item_name TEXT NOT NULL,
    price     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    author_name TEXT,
    quote       TEXT NOT NULL,
    rating      INTEGER,
    proof_url   TEXT,
    featured    INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dish_reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL,
    item_id     TEXT NOT NULL,
    rating      INTEGER NOT NULL,
    comment     TEXT,
    author_name TEXT,
    hidden      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(order_id, item_id)
);
CREATE TABLE IF NOT EXISTS wa_relay (
    wamid       TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    customer    TEXT,
    order_id    INTEGER,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS platform_stats (
    platform     TEXT PRIMARY KEY,
    rating       REAL,
    review_count INTEGER,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS site_stats (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    order_count INTEGER,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS store_status (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    is_open       INTEGER NOT NULL DEFAULT 1,
    reason        TEXT,
    turn_on_time  TEXT,
    updated_at    TEXT DEFAULT (datetime('now'))
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    Path(DB_FILE).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that may not exist in older databases."""
    def _has_col(table: str, col: str) -> bool:
        return col in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]

    if not _has_col("customers", "pincode"):
        conn.execute("ALTER TABLE customers ADD COLUMN pincode TEXT")
    if not _has_col("orders", "delivery_address"):
        conn.execute("ALTER TABLE orders ADD COLUMN delivery_address TEXT")
    if not _has_col("orders", "delivery_pincode"):
        conn.execute("ALTER TABLE orders ADD COLUMN delivery_pincode TEXT")
    if not _has_col("orders", "sr_order_id"):
        conn.execute("ALTER TABLE orders ADD COLUMN sr_order_id INTEGER")
    if not _has_col("orders", "sr_awb"):
        conn.execute("ALTER TABLE orders ADD COLUMN sr_awb TEXT")
    if not _has_col("orders", "sr_courier"):
        conn.execute("ALTER TABLE orders ADD COLUMN sr_courier TEXT")
    if not _has_col("orders", "sr_tracking_url"):
        conn.execute("ALTER TABLE orders ADD COLUMN sr_tracking_url TEXT")
    if not _has_col("orders", "dispatched_at"):
        conn.execute("ALTER TABLE orders ADD COLUMN dispatched_at TEXT")
    if not _has_col("orders", "delivery_lat"):
        conn.execute("ALTER TABLE orders ADD COLUMN delivery_lat TEXT")
    if not _has_col("orders", "delivery_lng"):
        conn.execute("ALTER TABLE orders ADD COLUMN delivery_lng TEXT")
    if not _has_col("orders", "address_flagged"):
        conn.execute("ALTER TABLE orders ADD COLUMN address_flagged INTEGER NOT NULL DEFAULT 0")
    if not _has_col("orders", "address_flag_reason"):
        conn.execute("ALTER TABLE orders ADD COLUMN address_flag_reason TEXT")
    if not _has_col("orders", "packing_fee"):
        conn.execute("ALTER TABLE orders ADD COLUMN packing_fee REAL NOT NULL DEFAULT 0")
    if not _has_col("orders", "gst_amount"):
        conn.execute("ALTER TABLE orders ADD COLUMN gst_amount REAL NOT NULL DEFAULT 0")
    if not _has_col("orders", "petpooja_order_id"):
        conn.execute("ALTER TABLE orders ADD COLUMN petpooja_order_id TEXT")
    if not _has_col("orders", "petpooja_synced_at"):
        conn.execute("ALTER TABLE orders ADD COLUMN petpooja_synced_at TEXT")
    if not _has_col("orders", "tracking_token"):
        conn.execute("ALTER TABLE orders ADD COLUMN tracking_token TEXT")
    if not _has_col("orders", "scheduled_window"):
        conn.execute("ALTER TABLE orders ADD COLUMN scheduled_window TEXT")
    if not _has_col("orders", "pay_courier_direct"):
        conn.execute("ALTER TABLE orders ADD COLUMN pay_courier_direct INTEGER NOT NULL DEFAULT 0")
    # Delivery settled after booking (Oct 2026): estimate range shown at
    # checkout, the real Porter fare recorded once the rider is booked.
    for col, ddl in (("delivery_fee_low", "REAL"), ("delivery_fee_high", "REAL"),
                     ("delivery_fee_final", "REAL"), ("courier_cost", "REAL"),
                     ("delivery_paid", "INTEGER NOT NULL DEFAULT 0")):
        if not _has_col("orders", col):
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
    # Discount codes (Oct 2026): what was taken off and which code did it.
    for col, ddl in (("discount_total", "REAL NOT NULL DEFAULT 0"), ("discount_code", "TEXT")):
        if not _has_col("orders", col):
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
    conn.execute("""CREATE TABLE IF NOT EXISTS discount_codes (
        code TEXT PRIMARY KEY,           -- stored upper-case
        kind TEXT NOT NULL DEFAULT 'percent',  -- 'percent' or 'flat' (rupees)
        value REAL NOT NULL,
        max_discount REAL,               -- cap in rupees for percent codes
        min_subtotal REAL NOT NULL DEFAULT 0,
        once_per_customer INTEGER NOT NULL DEFAULT 1,
        first_order_only INTEGER NOT NULL DEFAULT 0,
        max_uses INTEGER,                -- across everyone; NULL = unlimited
        active INTEGER NOT NULL DEFAULT 1,
        expires_at TEXT,                 -- ISO date/time, IST; NULL = never
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_discount_code ON orders(discount_code)")
    conn.execute("""CREATE TABLE IF NOT EXISTS order_feedback (
        order_id INTEGER PRIMARY KEY,
        rating TEXT NOT NULL,            -- good | ok | bad
        comment TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_tracking_token ON orders(tracking_token)")
    # Backfill a token for any older orders that predate this column, so every
    # order — new and old — gets a guess-proof tracking reference.
    missing = conn.execute(
        "SELECT id FROM orders WHERE tracking_token IS NULL OR tracking_token = ''"
    ).fetchall()
    if missing:
        import secrets as _secrets
        _alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for row in missing:
            token = "".join(_secrets.choice(_alphabet) for _ in range(8))
            conn.execute("UPDATE orders SET tracking_token=? WHERE id=?", (token, row["id"]))
    # store_status table (may not exist in older databases)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS store_status ("
        "    id            INTEGER PRIMARY KEY CHECK (id = 1),"
        "    is_open       INTEGER NOT NULL DEFAULT 1,"
        "    reason        TEXT,"
        "    turn_on_time  TEXT,"
        "    updated_at    TEXT DEFAULT (datetime('now'))"
        ")"
    )
    # daily_specials table (may not exist in older databases)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_specials ("
        "    date      TEXT PRIMARY KEY,"
        "    item_name TEXT NOT NULL,"
        "    price     REAL NOT NULL"
        ")"
    )


# ---- availability ----

def get_available_ids(day: str) -> set[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item_id, available FROM availability WHERE date=?", (day,)
    ).fetchall()
    conn.close()
    return {r["item_id"] for r in rows if r["available"] == 1}


def last_available_day() -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(date) AS d FROM availability WHERE date < date('now')"
    ).fetchone()
    conn.close()
    return row["d"] if row and row["d"] else None


def set_availability(day: str, available_ids: list[str], unavailable_ids: list[str]) -> None:
    conn = get_conn()
    for item_id in available_ids:
        conn.execute(
            "INSERT INTO availability(date, item_id, available) VALUES(?,?,1) "
            "ON CONFLICT(date, item_id) DO UPDATE SET available=1",
            (day, item_id),
        )
    for item_id in unavailable_ids:
        conn.execute(
            "INSERT INTO availability(date, item_id, available) VALUES(?,?,0) "
            "ON CONFLICT(date, item_id) DO UPDATE SET available=0",
            (day, item_id),
        )
    conn.commit()
    conn.close()


def copy_availability(from_day: str, to_day: str) -> int:
    """Copy a saved day's availability to another day. Returns item count copied."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT item_id, available FROM availability WHERE date=?", (from_day,)
    ).fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO availability(date, item_id, available) VALUES(?,?,?) "
            "ON CONFLICT(date, item_id) DO UPDATE SET available=excluded.available",
            (to_day, r["item_id"], r["available"]),
        )
    conn.commit()
    conn.close()
    return len(rows)


# ---- customers & orders ----

def upsert_customer(phone: str, name: str, address: str | None = None,
                     pincode: str | None = None) -> int:
    conn = get_conn()
    conn.execute(
        "INSERT INTO customers(phone, name, address, pincode) VALUES(?,?,?,?) "
        "ON CONFLICT(phone) DO UPDATE SET name=excluded.name, "
        "address=COALESCE(excluded.address, customers.address), "
        "pincode=COALESCE(excluded.pincode, customers.pincode)",
        (phone, name, address, pincode),
    )
    conn.commit()
    cid = conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()["id"]
    conn.close()
    return cid


def get_customer(phone: str) -> dict | None:
    """Get customer record by phone number.

    Exact match first, then on the last 10 digits, so "07557212240",
    "+91 75572 12240" and "7557212240" all find the same customer (stored
    phones are a historical mix, see get_orders_by_phone)."""
    import re as _re
    conn = get_conn()
    row = conn.execute(
        "SELECT id, phone, name, address, pincode FROM customers WHERE phone=?",
        (phone,),
    ).fetchone()
    digits = _re.sub(r"\D", "", phone or "")
    if not row and len(digits) >= 10:
        row = conn.execute(
            "SELECT id, phone, name, address, pincode FROM customers "
            "WHERE substr(replace(replace(phone,'+',''),' ',''), -10) = ? "
            "ORDER BY id DESC LIMIT 1",
            (digits[-10:],),
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_customer_addresses(customer_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, label, address, landmark, lat, lng, is_default FROM customer_addresses "
        "WHERE customer_id=? ORDER BY is_default DESC, created_at DESC",
        (customer_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_customer_address(customer_id: int, address: str, landmark: str | None = None,
                         lat: str | None = None, lng: str | None = None,
                         label: str | None = None) -> None:
    """Save a delivery address for future reuse, skipping exact duplicates."""
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM customer_addresses WHERE customer_id=? AND address=?",
        (customer_id, address),
    ).fetchone()
    if not exists:
        is_default = not conn.execute(
            "SELECT 1 FROM customer_addresses WHERE customer_id=?", (customer_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO customer_addresses(customer_id, label, address, landmark, lat, lng, is_default) "
            "VALUES(?,?,?,?,?,?,?)",
            (customer_id, label, address, landmark, lat, lng, 1 if is_default else 0),
        )
        conn.commit()
    conn.close()


def create_order(customer_id: int, order_type: str, subtotal: float,
                 delivery_fee: float, total: float, payment_method: str,
                 instructions: str | None, items: list[dict],
                 delivery_address: str | None = None,
                 delivery_pincode: str | None = None,
                 delivery_lat: str | None = None,
                 delivery_lng: str | None = None,
                 scheduled_at: str | None = None,
                 address_flagged: bool = False,
                 address_flag_reason: str | None = None,
                 packing_fee: float = 0.0,
                 gst_amount: float = 0.0,
                 tracking_token: str | None = None,
                 scheduled_window: str | None = None,
                 pay_courier_direct: bool = False,
                 discount_total: float = 0.0,
                 discount_code: str | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO orders(customer_id, status, order_type, subtotal, delivery_fee, "
        "packing_fee, gst_amount, total, payment_method, instructions, delivery_address, "
        "delivery_pincode, delivery_lat, delivery_lng, scheduled_at, address_flagged, "
        "address_flag_reason, tracking_token, scheduled_window, pay_courier_direct, "
        "discount_total, discount_code) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (customer_id, "new", order_type, subtotal, delivery_fee, packing_fee, gst_amount,
         total, payment_method, instructions, delivery_address, delivery_pincode,
         delivery_lat, delivery_lng, scheduled_at,
         1 if address_flagged else 0, address_flag_reason, tracking_token,
         scheduled_window, 1 if pay_courier_direct else 0,
         discount_total or 0.0, discount_code),
    )
    oid = cur.lastrowid
    conn.executemany(
        "INSERT INTO order_items(order_id, item_id, name, price, qty) VALUES(?,?,?,?,?)",
        [(oid, i["item_id"], i["name"], i["price"], i["qty"]) for i in items],
    )
    conn.commit()
    conn.close()
    return oid


def update_order_address(order_id: int, address: str, lat: str | None, lng: str | None,
                         address_flagged: bool = False, address_flag_reason: str | None = None) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET delivery_address=?, delivery_lat=?, delivery_lng=?, "
        "address_flagged=?, address_flag_reason=? WHERE id=?",
        (address, lat, lng, 1 if address_flagged else 0, address_flag_reason, order_id),
    )
    conn.commit()
    conn.close()


def update_order_dispatch(order_id: int, sr_order_id: int, awb: str,
                          courier: str, tracking_url: str) -> None:
    """Store Shiprocket dispatch details and mark as out_for_delivery."""
    from datetime import datetime
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET status='out_for_delivery', sr_order_id=?, sr_awb=?, "
        "sr_courier=?, sr_tracking_url=?, dispatched_at=? WHERE id=?",
        (sr_order_id, awb, courier, tracking_url, datetime.utcnow().isoformat(), order_id),
    )
    conn.commit()
    conn.close()


def set_delivery_estimate(order_id: int, low: float | None, high: float | None) -> None:
    conn = get_conn()
    conn.execute("UPDATE orders SET delivery_fee_low=?, delivery_fee_high=? WHERE id=?",
                 (low, high, order_id))
    conn.commit()
    conn.close()


def set_courier_cost(order_id: int, cost: float) -> None:
    """What the kitchen paid Porter for a fixed-fee order (customer not charged)."""
    conn = get_conn()
    conn.execute("UPDATE orders SET courier_cost=? WHERE id=?", (cost, order_id))
    conn.commit()
    conn.close()


def set_delivery_fee_final(order_id: int, fee: float) -> None:
    """The actual Porter fare, once known. Kept separate from `total` (the
    food amount the customer paid at checkout / owes in cash)."""
    conn = get_conn()
    conn.execute("UPDATE orders SET delivery_fee=?, delivery_fee_final=? WHERE id=?",
                 (fee, fee, order_id))
    conn.commit()
    conn.close()


def get_order(order_id: int) -> dict | None:
    conn = get_conn()
    o = conn.execute(
        "SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, "
        "       c.address AS customer_address "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE o.id=?",
        (order_id,),
    ).fetchone()
    if not o:
        conn.close()
        return None
    items = conn.execute(
        "SELECT item_id, name, price, qty FROM order_items WHERE order_id=?",
        (order_id,),
    ).fetchall()
    conn.close()
    d = dict(o)
    d["items"] = [dict(i) for i in items]
    return d


def get_order_by_petpooja(petpooja_order_id: str) -> dict | None:
    """Look up an order by the id Petpooja's order-callback sends.

    The callback's `orderID` is Petpooja's own order number (the `orderID`
    echoed back by Save Order), not our sequential id — so callbacks for
    statuses like POS-accept / food-ready must match on the stored
    `petpooja_order_id` instead of our `id`."""
    conn = get_conn()
    o = conn.execute(
        "SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, "
        "       c.address AS customer_address "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE o.petpooja_order_id=?",
        (str(petpooja_order_id),),
    ).fetchone()
    if not o:
        conn.close()
        return None
    items = conn.execute(
        "SELECT item_id, name, price, qty FROM order_items WHERE order_id=?",
        (o["id"],),
    ).fetchall()
    conn.close()
    d = dict(o)
    d["items"] = [dict(i) for i in items]
    return d


def get_order_by_token(tracking_token: str) -> dict | None:
    """Look up an order by its unguessable tracking token (public tracking)."""
    conn = get_conn()
    o = conn.execute(
        "SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, "
        "       c.address AS customer_address "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE o.tracking_token=?", (tracking_token,),
    ).fetchone()
    if not o:
        conn.close()
        return None
    items = conn.execute(
        "SELECT item_id, name, price, qty FROM order_items WHERE order_id=?",
        (o["id"],),
    ).fetchall()
    conn.close()
    d = dict(o)
    d["items"] = [dict(i) for i in items]
    return d


def get_orders_by_phone(phone: str, limit: int = 5) -> list[dict]:
    """Recent orders for a phone number (for the /track lookup). Cancelled
    orders excluded so customers only see live ones.

    `phone` comes from app.orders._normalize_phone (10 digits, or a full
    international number, of which the last 10 are matched).
    Matches on the last 10 digits of the stored value with '+'/spaces
    stripped, not on exact equality — `customers.phone` has historically
    accumulated a mix of "+91XXXXXXXXXX" / "91XXXXXXXXXX" / bare
    "XXXXXXXXXX" (pre-dating normalization at write time), and this keeps
    lookups working for those existing rows without a data migration."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.id, o.tracking_token, o.status, o.total, o.created_at, "
        "       o.scheduled_at, o.scheduled_window, o.order_type, "
        "       o.delivery_fee, o.pay_courier_direct "
        "FROM orders o JOIN customers c ON c.id=o.customer_id "
        "WHERE substr(replace(replace(c.phone,'+',''),' ',''), -10) = ? "
        "AND o.status != 'cancelled' "
        "ORDER BY o.id DESC LIMIT ?",
        (phone[-10:], limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_order_status(order_id: int, status: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()


def mark_order_paid(order_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE orders SET paid=1 WHERE id=?", (order_id,))
    conn.commit()
    conn.close()


def update_order_petpooja(order_id: int, petpooja_order_id: str) -> None:
    """Record the Petpooja POS order id after a successful Save Order push."""
    from datetime import datetime
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET petpooja_order_id=?, petpooja_synced_at=? WHERE id=?",
        (petpooja_order_id, datetime.utcnow().isoformat(), order_id),
    )
    conn.commit()
    conn.close()


# ---- store status (Petpooja "Update Store Status" push target) ----

def get_store_status() -> dict:
    conn = get_conn()
    row = conn.execute("SELECT is_open, reason, turn_on_time FROM store_status WHERE id=1").fetchone()
    conn.close()
    if not row:
        return {"is_open": True, "reason": None, "turn_on_time": None}
    return {"is_open": bool(row["is_open"]), "reason": row["reason"], "turn_on_time": row["turn_on_time"]}


def set_store_status(is_open: bool, reason: str | None = None, turn_on_time: str | None = None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO store_status(id, is_open, reason, turn_on_time, updated_at) "
        "VALUES(1, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET is_open=excluded.is_open, reason=excluded.reason, "
        "turn_on_time=excluded.turn_on_time, updated_at=excluded.updated_at",
        (1 if is_open else 0, reason, turn_on_time),
    )
    conn.commit()
    conn.close()


def recent_orders(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.id, o.status, o.order_type, o.total, o.payment_method, o.created_at, "
        "       c.name, c.phone "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "ORDER BY o.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def lifetime_order_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM orders WHERE status != 'cancelled'").fetchone()[0]
    conn.close()
    return n


def customer_orders(phone: str, limit: int = 5) -> list[dict]:
    """Get recent orders for a customer (for reorder)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.id, o.status, o.total, o.order_type, o.created_at "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE c.phone=? AND o.status != 'cancelled' "
        "ORDER BY o.id DESC LIMIT ?",
        (phone[-10:], limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _orders_with_items(rows) -> list[dict]:
    result = []
    for r in rows:
        d = dict(r)
        ic = get_conn()
        items = ic.execute(
            "SELECT item_id, name, price, qty FROM order_items WHERE order_id=?",
            (d["id"],),
        ).fetchall()
        ic.close()
        d["items"] = [dict(i) for i in items]
        result.append(d)
    return result


def today_orders() -> list[dict]:
    """Orders for the admin dashboard's main view: placed today, OR placed
    earlier but scheduled for today (a 2-week-ahead order has to actually
    surface on the day it's due, not just on the day it was created)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.*, c.name AS customer_name, c.phone AS customer_phone "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE date(o.created_at) = date('now') "
        "   OR (o.scheduled_at IS NOT NULL AND date(o.scheduled_at) = date('now')) "
        "ORDER BY o.id DESC"
    ).fetchall()
    result = _orders_with_items(rows)
    conn.close()
    return result


def set_feedback(order_id: int, rating: str | None = None, comment: str | None = None) -> None:
    """Record a customer's one-tap rating, then optionally their words."""
    conn = get_conn()
    if rating:
        conn.execute("INSERT INTO order_feedback(order_id, rating) VALUES(?, ?) "
                     "ON CONFLICT(order_id) DO UPDATE SET rating=excluded.rating", (order_id, rating))
    if comment:
        conn.execute("UPDATE order_feedback SET comment=? WHERE order_id=?", (comment[:1000], order_id))
    conn.commit()
    conn.close()


def get_feedback(order_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM order_feedback WHERE order_id=?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def kitchen_orders() -> list[dict]:
    """Everything the kitchen screen shows: every open order (whenever it was
    placed or is due), plus today's finished ones. "Today" is the IST day."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.*, c.name AS customer_name, c.phone AS customer_phone "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE o.status NOT IN ('delivered', 'cancelled') "
        "   OR date(o.created_at, '+330 minutes') = date('now', '+330 minutes') "
        "   OR (o.scheduled_at IS NOT NULL AND date(o.scheduled_at, '+330 minutes') = date('now', '+330 minutes')) "
        "ORDER BY o.id DESC LIMIT 200"
    ).fetchall()
    result = _orders_with_items(rows)
    conn.close()
    return result


def upcoming_scheduled_orders() -> list[dict]:
    """Every not-yet-fulfilled order with a scheduled time, regardless of
    which day it was placed on — the Scheduled tab's planning view. Without
    this, an order placed today for 10 days out would vanish from every
    admin view in between (today_orders() only ever looks at today)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT o.*, c.name AS customer_name, c.phone AS customer_phone "
        "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE o.scheduled_at IS NOT NULL AND o.status NOT IN ('delivered', 'cancelled') "
        "ORDER BY o.scheduled_at ASC"
    ).fetchall()
    result = _orders_with_items(rows)
    conn.close()
    return result


def seeded() -> None:
    """Seed menu ids into availability so 'today's menu' = full menu until toggled."""
    from .menu import load_menu
    menu = load_menu()
    today = date.today().isoformat()
    ids = [m["id"] for m in menu]
    set_availability(today, ids, [])


# ---- daily specials ----

def get_special(day: str | None = None) -> dict | None:
    day = day or date.today().isoformat()
    conn = get_conn()
    row = conn.execute(
        "SELECT item_name, price FROM daily_specials WHERE date=?", (day,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_special(day: str, item_name: str, price: float) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO daily_specials(date, item_name, price) VALUES(?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET item_name=excluded.item_name, price=excluded.price",
        (day, item_name, price),
    )
    conn.commit()
    conn.close()


def clear_special(day: str | None = None) -> None:
    day = day or date.today().isoformat()
    conn = get_conn()
    conn.execute("DELETE FROM daily_specials WHERE date=?", (day,))
    conn.commit()
    conn.close()


# ---- reviews ----

def list_reviews() -> list[dict]:
    """All reviews, newest first — for the admin tab."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM reviews ORDER BY featured DESC, sort_order DESC, id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_featured_reviews(limit: int = 6) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM reviews WHERE featured=1 ORDER BY sort_order DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_review(source: str, quote: str, author_name: str | None = None,
              rating: int | None = None, proof_url: str | None = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO reviews(source, author_name, quote, rating, proof_url) VALUES(?,?,?,?,?)",
        (source, author_name, quote, rating, proof_url),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def delete_review(review_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM reviews WHERE id=?", (review_id,))
    conn.commit()
    conn.close()


def set_review_featured(review_id: int, featured: bool) -> None:
    conn = get_conn()
    conn.execute("UPDATE reviews SET featured=? WHERE id=?", (1 if featured else 0, review_id))
    conn.commit()
    conn.close()


# ---- platform stats (manual entry, Google auto-refreshed) ----

def get_platform_stats() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM platform_stats").fetchall()
    conn.close()
    return {r["platform"]: dict(r) for r in rows}


def set_platform_stats(platform: str, rating: float | None, review_count: int | None) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO platform_stats(platform, rating, review_count, updated_at) "
        "VALUES(?,?,?,datetime('now')) "
        "ON CONFLICT(platform) DO UPDATE SET rating=excluded.rating, "
        "review_count=excluded.review_count, updated_at=excluded.updated_at",
        (platform, rating, review_count),
    )
    conn.commit()
    conn.close()


# ---- site-wide order count estimate (marketing number, not a live tally —
# real order history spans years across marketplaces/walk-in/phone, none
# of which this app has ever recorded, so there's no honest way to compute
# it — an admin-set approximate figure beats a misleadingly tiny real count) ----

def get_order_count_estimate() -> int | None:
    conn = get_conn()
    row = conn.execute("SELECT order_count FROM site_stats WHERE id=1").fetchone()
    conn.close()
    return row["order_count"] if row else None


def set_order_count_estimate(count: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO site_stats(id, order_count, updated_at) VALUES(1, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET order_count=excluded.order_count, updated_at=excluded.updated_at",
        (count,),
    )
    conn.commit()
    conn.close()


# ---- dish reviews (verified: one per dish per delivered order) ----

def add_dish_review(order_id: int, item_id: str, rating: int,
                    comment: str | None, author_name: str | None) -> bool:
    """False if this order already rated this dish."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO dish_reviews(order_id, item_id, rating, comment, author_name) "
            "VALUES(?,?,?,?,?)", (order_id, item_id, rating, comment, author_name))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def order_reviewed_items(order_id: int) -> set[str]:
    conn = get_conn()
    rows = conn.execute("SELECT item_id FROM dish_reviews WHERE order_id=?", (order_id,)).fetchall()
    conn.close()
    return {r["item_id"] for r in rows}


def dish_rating(item_id: str) -> dict:
    """{'count', 'average'} over visible reviews of a dish (base id)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n, AVG(rating) AS avg FROM dish_reviews WHERE item_id=? AND hidden=0",
        (item_id,)).fetchone()
    conn.close()
    return {"count": row["n"] or 0, "average": round(row["avg"], 1) if row["avg"] else None}


def dish_reviews(item_id: str, limit: int = 5) -> list[dict]:
    """Newest visible reviews that have a comment, for the dish page."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, rating, comment, author_name, created_at FROM dish_reviews "
        "WHERE item_id=? AND hidden=0 AND comment IS NOT NULL AND comment != '' "
        "ORDER BY id DESC LIMIT ?", (item_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_dish_reviews(limit: int = 200) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM dish_reviews ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_dish_review_hidden(review_id: int, hidden: bool) -> None:
    conn = get_conn()
    conn.execute("UPDATE dish_reviews SET hidden=? WHERE id=?", (1 if hidden else 0, review_id))
    conn.commit()
    conn.close()


# ---- WhatsApp relay: which customer/order a message to the owner was about ----

def add_relay(wamid: str, kind: str, customer: str | None = None,
              order_id: int | None = None) -> None:
    if not wamid:
        return
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO wa_relay(wamid, kind, customer, order_id) VALUES(?,?,?,?)",
                 (wamid, kind, customer, order_id))
    conn.commit()
    conn.close()


def get_relay(wamid: str | None) -> dict | None:
    if not wamid:
        return None
    conn = get_conn()
    row = conn.execute("SELECT * FROM wa_relay WHERE wamid=?", (wamid,)).fetchone()
    conn.close()
    return dict(row) if row else None
