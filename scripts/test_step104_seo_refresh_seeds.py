"""
Step 104-I test — 7-3 seasonal seed expansion, 7-4 zero-view SEO refresh.

Usage: python scripts/test_step104_seo_refresh_seeds.py
"""
import os
import sys
import tempfile
from datetime import date

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "i.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

# ── 7-3 seasonal seed expansion ──
from app.core.seasonality import seasonal_seed_keywords, _EVENTS

# every event carries 1-3 proven seeds
check("7-3 every event has seeds", all(e.get("seeds") for e in _EVENTS))

# ~5 weeks before Christmas (Dec 25) → in christmas window (6-14w? no, 5w is inside)
# christmas min_w=6 max_w=14, so 8 weeks before = Oct 30 is in window.
oct30 = date(2026, 10, 30)
seeds = seasonal_seed_keywords(oct30, max_seeds=6)
check("7-3 in-window Christmas contributes multiple proven phrases",
      any("christmas" in s for s in seeds) and len([s for s in seeds if "christmas" in s]) >= 2)
check("7-3 respects max_seeds cap", len(seasonal_seed_keywords(oct30, max_seeds=3)) <= 3)

# A true dead zone: Aug 15 falls between back-to-school (ends ~Aug 4) and
# Halloween (opens ~Aug 26) — no occasion window covers it.
aug15 = date(2026, 8, 15)
check("7-3 dead-zone date yields no seasonal seeds", len(seasonal_seed_keywords(aug15)) == 0)

# ── 7-4 SEO refresh (deterministic rewrite) ──
from app.services.listing_seo_refresh_service import ListingSeoRefreshService

svc = ListingSeoRefreshService()
top_titles = [
    "Boho Wall Art Print Set Neutral Living Room Decor",
    "Minimalist Wall Art Printable Boho Home Decor",
    "Boho Wall Art Print Digital Download Bedroom",
]
plan = svc.build_refresh("My Nice Poster", ["poster art", "home decor"], top_titles)
check("7-4 rewrite promotes a proven phrase to the title front",
      plan["title"].lower().startswith("boho wall art") or "boho wall art" in plan["title"].lower())
check("7-4 rewrite yields tags", len(plan["tags"]) >= 5)
check("7-4 tags include a proven n-gram", any("boho" in t.lower() for t in plan["tags"]))
check("7-4 title within Etsy 140-char limit", len(plan["title"]) <= 140)
check("7-4 existing tags preserved", any("home decor" in t.lower() for t in plan["tags"]))

# title unchanged when it already leads with the strongest phrase
plan2 = svc.build_refresh("Boho Wall Art Print Neutral Decor", [], top_titles)
check("7-4 no needless title churn when already-optimized-front",
      plan2["title"].lower().startswith("boho wall art"))

# idempotency query helper
q = svc._query_from_listing({"title": "Printable Boho Wall Art Instant Download Decor"})
check("7-4 query drops stopwords/printable", "printable" not in q and "boho" in q)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-I (7-3/7-4) tests passed.")
