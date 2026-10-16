"""SQLite-backed WhatsApp sessions.

Each wa_id keeps a lightweight conversation state: where in the flow the
customer is, their cart, and half-finished checkout fields. Survives restarts.
"""
import json
import sqlite3
from datetime import datetime

from ..config import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS wa_sessions (
    wa_id       TEXT PRIMARY KEY,
    state       TEXT NOT NULL DEFAULT 'root',
    cart        TEXT NOT NULL DEFAULT '{}',
    ctx         TEXT NOT NULL DEFAULT '{}',
    human       INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT DEFAULT (datetime('now'))
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_sessions() -> None:
    conn = _conn()
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(wa_sessions)")]
    if "human" not in cols:
        conn.execute("ALTER TABLE wa_sessions ADD COLUMN human INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def is_human(wa_id: str) -> bool:
    """True when a human has taken over this chat (bot stays quiet)."""
    conn = _conn()
    row = conn.execute("SELECT human FROM wa_sessions WHERE wa_id=?", (wa_id,)).fetchone()
    conn.close()
    return bool(row and row["human"])


def set_human(wa_id: str, value: bool) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO wa_sessions(wa_id, human) VALUES(?,?) "
        "ON CONFLICT(wa_id) DO UPDATE SET human=excluded.human",
        (wa_id, 1 if value else 0),
    )
    conn.commit()
    conn.close()


def all_sessions() -> list[dict]:
    """Recent conversations for the admin panel."""
    conn = _conn()
    rows = conn.execute(
        "SELECT wa_id, human, ctx, updated_at FROM wa_sessions "
        "ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            ctx = json.loads(r["ctx"])
        except ValueError:
            ctx = {}
        out.append({
            "wa_id": r["wa_id"],
            "human": bool(r["human"]),
            "name": ctx.get("profile_name") or ctx.get("name") or "",
            "updated_at": r["updated_at"],
        })
    return out


def load(wa_id: str) -> dict:
    conn = _conn()
    row = conn.execute("SELECT * FROM wa_sessions WHERE wa_id=?", (wa_id,)).fetchone()
    conn.close()
    if not row:
        return {"wa_id": wa_id, "state": "root", "cart": {}, "ctx": {}}
    return {
        "wa_id": row["wa_id"],
        "state": row["state"],
        "cart": json.loads(row["cart"]),
        "ctx": json.loads(row["ctx"]),
    }


def save(sess: dict) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO wa_sessions(wa_id, state, cart, ctx, updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(wa_id) DO UPDATE SET state=excluded.state, cart=excluded.cart, "
        "ctx=excluded.ctx, updated_at=excluded.updated_at",
        (sess["wa_id"], sess["state"], json.dumps(sess["cart"]),
         json.dumps(sess["ctx"]), datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def reset(wa_id: str) -> None:
    conn = _conn()
    conn.execute("DELETE FROM wa_sessions WHERE wa_id=?", (wa_id,))
    conn.commit()
    conn.close()
