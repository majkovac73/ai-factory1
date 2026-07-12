"""
Step 105-C test — 1-6 expanded event table, 1-7 occasion backfill.

Usage: python scripts/test_step105_seasonality_backfill.py
"""
import os
import sys
import tempfile
from datetime import date

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s105c.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


from app.core.seasonality import (
    occasion_mismatch, occasion_for, occasion_in_window, hanukkah, _DIWALI,
    upcoming_occasions, seasonal_seed_keywords,
)

# ── 1-6: new occasions exist and gate correctly ──
# Cinco de Mayo (May 5) proposed on July 12 → out of window → rejected
jul12 = date(2026, 7, 12)
check("1-6 occasion_for stamps cinco de mayo",
      occasion_for("Cinco de Mayo Fiesta Coloring Page", "papel picado fiesta") == "cinco_de_mayo")
check("1-6 Cinco de Mayo rejected in July",
      occasion_mismatch("Cinco de Mayo Fiesta Coloring Page", "fiesta", jul12) is not None)

# 4th of July: rejected in December, accepted in early June
dec1 = date(2026, 12, 1)
jun6 = date(2026, 6, 6)
check("1-6 occasion_for stamps july_4th",
      occasion_for("Stars and Stripes Summer Printable", "patriotic 4th of july") == "july_4th")
check("1-6 4th of July rejected in December",
      occasion_mismatch("4th of July BBQ Printable", "independence day", dec1) is not None)
check("1-6 4th of July accepted in early June",
      occasion_mismatch("4th of July BBQ Printable", "independence day", jun6) is None)

# Hanukkah exists + movable table
check("1-6 hanukkah 2026 = Dec 5", hanukkah(2026) == date(2026, 12, 5))
check("1-6 occasion_for stamps hanukkah", occasion_for("Menorah Hanukkah Print", "dreidel") == "hanukkah")

# Diwali
check("1-6 diwali table has 2026", _DIWALI.get(2026) == date(2026, 11, 8))
check("1-6 occasion_for stamps diwali", occasion_for("Diwali Rangoli Printable", "deepavali") == "diwali")

# Weddings: match-only, year-round → never rejected, never seeded
check("1-6 occasion_for stamps weddings", occasion_for("Bridal Shower Sign", "wedding welcome") == "weddings")
check("1-6 weddings never rejected as out-of-season (Jan)",
      occasion_mismatch("Wedding Welcome Sign", "bridal shower", date(2026, 1, 15)) is None)
check("1-6 weddings never rejected as out-of-season (Sep)",
      occasion_mismatch("Wedding Welcome Sign", "bridal shower", date(2026, 9, 15)) is None)
check("1-6 weddings in-window year-round", occasion_in_window("weddings", jul12) is True)
# match-only excluded from seed pool and the shop-now list
upco = upcoming_occasions(jul12)
check("1-6 weddings NOT in upcoming_occasions (match-only)",
      all(o["key"] != "weddings" for o in upco))
seeds = seasonal_seed_keywords(date(2026, 1, 10), max_seeds=10)
check("1-6 wedding phrases never seeded", all("wedding" not in s for s in seeds))

# NYE party distinct from new_year
check("1-6 occasion_for stamps nye_party",
      occasion_for("New Year's Eve Party Printable Kit", "nye party") == "nye_party")

# ── 1-7: backfill stamps DONE product tasks ──
db = SessionLocal()
db.add(Task(id="old_easter", prompt="p", type="coloring_page", status="DONE", input_data={},
            output_data={"title": "Easter Bunny Coloring Page", "description": "cute easter bunny"},
            metadata_={"product_format": "coloring_page"}))
db.add(Task(id="evergreen", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={"title": "Minimalist Mountain Print", "description": "calm mountains"},
            metadata_={"product_format": "single_print"}))
db.add(Task(id="already", prompt="p", type="coloring_page", status="DONE", input_data={},
            output_data={"title": "Halloween Ghost", "description": "spooky"},
            metadata_={"product_format": "coloring_page", "occasion": "halloween"}))
db.commit()
db.close()

import importlib.util
spec = importlib.util.spec_from_file_location(
    "backfill_occ", os.path.join(os.path.dirname(__file__), "backfill_occasion_metadata.py"))
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

dry = bf.run(apply=False)
stamped_ids = {s["task_id"] for s in dry["stamped"]}
check("1-7 dry-run finds the easter task", "old_easter" in stamped_ids)
check("1-7 dry-run skips evergreen", "evergreen" not in stamped_ids)
check("1-7 dry-run counts already-stamped", dry["already_stamped"] == 1)

# verify dry run did NOT write
db = SessionLocal()
t = db.query(Task).filter(Task.id == "old_easter").first()
check("1-7 dry-run wrote nothing", not (t.metadata_ or {}).get("occasion"))
db.close()

applied = bf.run(apply=True)
db = SessionLocal()
t = db.query(Task).filter(Task.id == "old_easter").first()
check("1-7 apply stamped occasion=easter", (t.metadata_ or {}).get("occasion") == "easter")
t2 = db.query(Task).filter(Task.id == "evergreen").first()
check("1-7 evergreen left unstamped", not (t2.metadata_ or {}).get("occasion"))
db.close()

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-105-C tests passed.")
