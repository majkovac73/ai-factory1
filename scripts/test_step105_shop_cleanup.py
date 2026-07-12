"""
Step 105-D test — 1-2 low-score shop cleanup from an audit report.

Usage: python scripts/test_step105_shop_cleanup.py
"""
import json
import os
import sys
import tempfile
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s105d.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.low_score_cleanup_service import LowScoreCleanupService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


tmp = tempfile.mkdtemp()
report_path = os.path.join(tmp, "2026-07-12.json")
rows = [
    {"listing_id": 1, "title": "Out of season Easter page", "score": 1, "passed": False},
    {"listing_id": 2, "title": "Bland Thanksgiving", "score": 3, "passed": False},
    {"listing_id": 3, "title": "Mediocre summer coloring", "score": 4, "passed": False},
    {"listing_id": 4, "title": "Okay planner", "score": 5, "passed": False},
    {"listing_id": 5, "title": "Solid boho print", "score": 6, "passed": True},
    {"listing_id": 6, "title": "Great constellation map", "score": 9, "passed": True},
]
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(rows, f)

svc = LowScoreCleanupService()

# ── dry run: categorizes, deactivates nothing ──
patched_updates = []


async def fake_update(self, listing_id, fields):
    patched_updates.append((listing_id, fields))
    return {"listing_id": listing_id}


with patch("app.services.etsy_client.EtsyClient.update_listing", fake_update):
    dry = svc.run(report_path=report_path, apply=False)

check("1-2 dry-run ok", dry["ok"])
check("1-2 deactivate list = the <=3 scorers (2)", {c["listing_id"] for c in dry["deactivate"]} == {"1", "2"})
check("1-2 seo_retry = the 4-5 scorers (2)", {c["listing_id"] for c in dry["seo_retry"]} == {"3", "4"})
check("1-2 keep = the 6+ scorers (2)", dry["keep"] == 2)
check("1-2 dry-run deactivated nothing (no API calls)", len(patched_updates) == 0)

# ── apply: deactivates only the <=3 scorers ──
with patch("app.services.etsy_client.EtsyClient.update_listing", fake_update):
    applied = svc.run(report_path=report_path, apply=True)

check("1-2 apply deactivated 2", applied["deactivated"] == 2)
check("1-2 apply called update_listing for 1 and 2 only",
      {u[0] for u in patched_updates} == {"1", "2"})
check("1-2 apply set state=inactive", all(u[1] == {"state": "inactive"} for u in patched_updates))

# ── threshold is configurable ──
from config import settings
with patch.object(settings, "SHOP_CLEANUP_MAX_SCORE", 5), \
     patch("app.services.etsy_client.EtsyClient.update_listing", fake_update):
    dry2 = svc.run(report_path=report_path, apply=False)
check("1-2 threshold knob raises the deactivate cut", {c["listing_id"] for c in dry2["deactivate"]} == {"1", "2", "3", "4"})

# ── missing report handled ──
missing = svc.run(report_path=os.path.join(tmp, "nope.json"), apply=False)
check("1-2 missing report returns not-ok", missing["ok"] is False)

# ── committed report exists at the documented path ──
check("1-2 audit report committed under instructions/audit_reports/",
      os.path.exists(os.path.join("instructions", "audit_reports", "2026-07-12.json")))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-105-D tests passed.")
