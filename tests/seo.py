"""SEO regression check: start the app, then run `python tests/seo.py`.

Guards the things that silently cost search traffic: a sitemap that errors,
non-indexable URLs listed in it, missing canonical/OG/structured data, and
private pages that are indexable.
"""
import json
import os
import re
import sys

import httpx

BASE = f"http://127.0.0.1:{os.environ.get('TEST_PORT', '8000')}"
c = httpx.Client(base_url=BASE, timeout=15)


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")
    if not cond:
        sys.exit(1)


def meta(html, pattern):
    m = re.search(pattern, html)
    return m.group(1) if m else None


sm = c.get("/sitemap.xml")
check("sitemap.xml returns 200", sm.status_code == 200, str(sm.status_code))
locs = re.findall(r"<loc>([^<]+)</loc>", sm.text)
check("sitemap lists pages", len(locs) >= 5, f"{len(locs)} urls")
check("sitemap has no duplicates", len(locs) == len(set(locs)))

robots = c.get("/robots.txt").text
check("robots.txt points at the sitemap", "/sitemap.xml" in robots)

for url in locs:
    path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
    r = c.get(path)
    check(f"{path} is 200", r.status_code == 200, str(r.status_code))
    h = r.text
    check(f"{path} has a title", bool(meta(h, r"<title>([^<]{5,})</title>")))
    check(f"{path} has a description", bool(meta(h, r'name="description" content="([^"]{20,})"')))
    check(f"{path} has a canonical", bool(meta(h, r'rel="canonical" href="([^"]+)"')))
    check(f"{path} is indexable", "noindex" not in h.lower().split("</head>")[0])
    check(f"{path} has one h1", len(re.findall(r"<h1[\s>]", h)) == 1)
    check(f"{path} has no duplicate og:title", h.count('property="og:title"') == 1)
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        try:
            json.loads(block)
        except ValueError:
            check(f"{path} JSON-LD parses", False)

home = c.get("/").text
check("home has Restaurant JSON-LD", '"@type": "Restaurant"' in home or '"Restaurant"' in home)
for path in ("/404", "/track"):
    check(f"{path} is noindex and not in sitemap",
          "noindex" in c.get(path).text and not any(u.endswith(path) for u in locs))
print("SEO OK")
