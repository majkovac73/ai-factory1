"""
Step 103 / C-5 test — listing-fee recording + dead-inventory pruning.

  [1] AutonomyService records a $0.20 spend entry (the listing-fee call shape).
  [2] ListingPruneService flags old zero-sale low-view listings, and EXCLUDES
      recent ones, high-view ones, and ones with a recorded sale. Dry-run does
      not deactivate.

Usage: python scripts/test_step103_fees_prune.py
"""
import os
import sys
import tempfile
import time
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "prune.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.image_asset import ImageAsset
from app.models.analytics_event import AnalyticsEvent  # noqa
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

# [1] fee recording shape
from app.services.autonomy_service import AutonomyService
auto = AutonomyService()
p = auto._state_path()
if p.exists():
    p.unlink()
before = auto.daily_status()["spend_usd"]
auto.record_spend(0.20, "etsy listing fee L1")
check("1 $0.20 listing fee recorded in ledger", abs(auto.daily_status()["spend_usd"] - before - 0.20) < 1e-9)

# [2] pruning
# map listings to tasks; give one task a sale
db = SessionLocal()
for lid, tid in [("100", "t-old-nosale"), ("101", "t-recent"), ("102", "t-highview"), ("103", "t-sold")]:
    db.add(ImageAsset(task_id=tid, listing_id=lid, variant="delivery", use_case="delivery",
                      agent="X", local_path=f"/x_{lid}.png"))
db.commit()
db.close()
from app.services.revenue_service import RevenueService
RevenueService().record_sale("t-sold", 5.0, transaction_id="tx-1")

now = time.time()
old = int(now - 200 * 86400)      # 200 days
recent = int(now - 10 * 86400)    # 10 days
listings = [
    {"listing_id": 100, "title": "Old no sale", "created_timestamp": old, "views": 2},        # PRUNE
    {"listing_id": 101, "title": "Recent", "created_timestamp": recent, "views": 1},           # keep (recent)
    {"listing_id": 102, "title": "Old high views", "created_timestamp": old, "views": 500},    # keep (views)
    {"listing_id": 103, "title": "Old but sold", "created_timestamp": old, "views": 1},         # keep (sold)
]

from app.services.listing_prune_service import ListingPruneService

async def _fake_fetch(self=None):
    return listings

with patch("app.services.listing_stats_service.ListingStatsService._fetch_active_listings", new=_fake_fetch), \
     patch("app.services.alert_service.AlertService"):
    report = ListingPruneService().run(apply=False)

ids = {c["listing_id"] for c in report["candidates"]}
check("2 old zero-sale low-view flagged", "100" in ids)
check("2 recent listing kept", "101" not in ids)
check("2 high-view listing kept", "102" not in ids)
check("2 sold listing kept", "103" not in ids)
check("2 dry-run deactivates nothing", report["deactivated"] == 0 and report["applied"] is False)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 fees+prune tests passed.")
