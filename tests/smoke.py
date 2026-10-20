"""Boot check: start the app (`uvicorn app.main:app --port 8000`), then run this.

    python tests/smoke.py

Set TEST_PORT and ADMIN_TOKEN if you changed them.
"""
import os
import sys

import httpx

PORT = os.environ.get("TEST_PORT", "8000")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = os.environ.get("ADMIN_TOKEN", "change-me")

PAGES = ["/", "/menu", "/about", "/how-to-order", "/delivery", "/privacy-policy",
         "/refund-cancellation-policy", "/updates", "/category", "/track", "/admin", "/kitchen"]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        sys.exit(1)


def main():
    print("smoke test")
    with httpx.Client(base_url=BASE, timeout=10) as c:
        for path in PAGES:
            r = c.get(path)
            check(f"GET {path}", r.status_code == 200, r.status_code)

        data = c.get("/api/menu").json()
        ids = [it["id"] for g in data["groups"] for it in g["items"]]
        check("menu has items", len(ids) > 0, len(ids))

        r = c.get(f"/menu/{ids[0]}")
        check("item page renders", r.status_code == 200, r.status_code)

        r = c.post("/api/admin/availability", json={"available_ids": ids, "unavailable_ids": []})
        check("admin without token rejected", r.status_code == 401, r.status_code)
        r = c.post("/api/admin/availability", json={"available_ids": ids[1:], "unavailable_ids": ids[:1]},
                   headers={"X-Admin-Token": TOKEN})
        check("admin with token accepted", r.status_code == 200, r.status_code)
        flags = {it["id"]: it["available"] for g in c.get("/api/menu").json()["groups"] for it in g["items"]}
        check("availability reflected", not flags[ids[0]])
        c.post("/api/admin/availability", json={"available_ids": ids, "unavailable_ids": []},
               headers={"X-Admin-Token": TOKEN})
    print("all passed")


if __name__ == "__main__":
    main()
