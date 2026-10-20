"""Delete every order except the one(s) to keep (default: #46). Dry-run unless --apply.

Run it on the machine that holds the live database, with the app stopped or idle:

    python scripts/purge_orders.py --keep 46            # shows what would go
    python scripts/purge_orders.py --keep 46 --apply    # backs up, then deletes

It copies the database next to itself (kitchen.db.bak-<timestamp>) before deleting.
Customers and saved addresses are left alone. Public dish reviews are left alone
unless --include-reviews is passed. Order ids are not reset, so the next order
continues after the highest id ever used.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DB_FILE  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, nargs="+", default=[46], help="order ids to keep")
    ap.add_argument("--keep-latest", type=int, default=0, help="also keep the N most recent orders (highest ids)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--include-reviews", action="store_true", help="also delete dish_reviews of removed orders")
    ap.add_argument("--db", default=str(DB_FILE))
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    keep = list(a.keep)
    if a.keep_latest:
        keep += [r[0] for r in conn.execute("SELECT id FROM orders ORDER BY id DESC LIMIT ?", (a.keep_latest,))]
        keep = sorted(set(keep))
    q = ",".join("?" * len(keep))
    present = [r[0] for r in conn.execute(f"SELECT id FROM orders WHERE id IN ({q})", keep)]
    missing = sorted(set(keep) - set(present))
    if missing:
        sys.exit(f"Refusing to run: order(s) {missing} not found in {a.db}. Wrong database?")

    total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    doomed = [r[0] for r in conn.execute(f"SELECT id FROM orders WHERE id NOT IN ({q})", keep)]
    print(f"DB: {a.db}\nOrders total {total}; keeping {sorted(present)}; deleting {len(doomed)}")
    for table in ("order_items", "order_feedback", "wa_relay", "dish_reviews"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE order_id IS NOT NULL AND order_id NOT IN ({q})", keep).fetchone()[0]
        except sqlite3.OperationalError:
            continue
        note = "" if table != "dish_reviews" or a.include_reviews else " (left alone)"
        print(f"  {table}: {n} rows tied to deleted orders{note}")
    if not a.apply:
        print("Dry run. Add --apply to delete.")
        return

    bak = f"{a.db}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    dst = sqlite3.connect(bak)
    conn.backup(dst)
    dst.close()
    print(f"Backup written: {bak}")
    tables = ["order_items", "order_feedback", "wa_relay"] + (["dish_reviews"] if a.include_reviews else [])
    with conn:
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table} WHERE order_id IS NOT NULL AND order_id NOT IN ({q})", keep)
            except sqlite3.OperationalError:
                pass
        conn.execute(f"DELETE FROM orders WHERE id NOT IN ({q})", keep)
    print("Remaining orders:", [r[0] for r in conn.execute("SELECT id FROM orders")])


if __name__ == "__main__":
    main()
