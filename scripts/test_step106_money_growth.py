"""
Step 106-F test — 3-2 cleanup exempts hero.png, 3-3 bands+clamp event,
3-4 offsite ads fee, 3-5 profit-by-format, 3-10 engagement variants.

Usage: python scripts/test_step106_money_growth.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s106f.db")
_data = tempfile.mkdtemp()
os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(_data, "images")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.models.analytics_event import AnalyticsEvent  # noqa
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 3-3: raised bands ──
from app.core.product_formats import price_band_for
check("3-3 single_print band raised to (3.50,10.00)", price_band_for("single_print") == (3.50, 10.00))
check("3-3 pdf band raised to (5.00,16.00)", price_band_for("pdf_planner_or_guide") == (5.00, 16.00))

# ── 3-4: offsite ads in the fee estimate ──
from app.services.revenue_service import RevenueService
rs = RevenueService()
# $10 sale: base = 0.65+0.30+0.25 = 1.20; offsite = 10*0.10*0.15 = 0.15 -> 1.35
fee = rs.record_fee_estimate(task_id="t10", sale_amount=10.0, transaction_id="txA")
check(f"3-4 fee includes offsite ads (expect 1.35, got {fee})", abs(fee - 1.35) < 1e-6)

# ── 3-5: profit_by_format ──
db = SessionLocal()
db.add(Task(id="tp", prompt="p", type="pdf_planner_or_guide", status="DONE", input_data={}, metadata_={}))
db.add(Task(id="tc", prompt="p", type="coloring_page", status="DONE", input_data={}, metadata_={}))
db.commit(); db.close()
rs.record_sale("tp", 12.0, transaction_id="s1"); rs.record_fee_estimate("tp", 12.0, transaction_id="s1")
rs.record_sale("tc", 3.0, transaction_id="s2"); rs.record_fee_estimate("tc", 3.0, transaction_id="s2")
pbf = rs.profit_by_format()
check("3-5 profit_by_format has pdf + coloring", "pdf_planner_or_guide" in pbf and "coloring_page" in pbf)
check("3-5 pdf net > coloring net", pbf["pdf_planner_or_guide"]["net"] > pbf["coloring_page"]["net"])
check("3-5 avg_price computed", pbf["pdf_planner_or_guide"]["avg_price"] == 12.0)

# ── 3-2: cleanup exempts hero.png of published products ──
from app.services.image_cleanup_service import ImageCleanupService
from PIL import Image
db = SessionLocal()
db.add(Task(id="pub", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={"listing_id": "999"}, metadata_={}))
db.add(Task(id="unpub", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={}, metadata_={}))
db.commit(); db.close()

lst = ImageCleanupService().images_dir / "listing"
for tid in ("pub", "unpub"):
    (lst / tid).mkdir(parents=True, exist_ok=True)
    for fn in ("hero.png", "lifestyle.png"):
        Image.new("RGB", (8, 8), (1, 1, 1)).save(lst / tid / fn)
# age everything past the listing cutoff
old = time.time() - (getattr(settings, "IMAGE_CLEANUP_LISTING_MAX_AGE_HOURS", 6) * 3600 + 3600)
for p in lst.rglob("*.png"):
    os.utime(p, (old, old))

with patch.object(settings, "IMAGE_CLEANUP_DELIVERY_MAX_AGE_DAYS", 3):
    ImageCleanupService().cleanup()

check("3-2 published hero.png kept", (lst / "pub" / "hero.png").exists())
check("3-2 published non-hero pruned", not (lst / "pub" / "lifestyle.png").exists())
check("3-2 unpublished hero.png pruned", not (lst / "unpub" / "hero.png").exists())

# ── 3-10: engagement-triggered variant ──
import app.workers.etsy_receipt_worker as erw
worker = erw.EtsyReceiptWorker(fulfillment_service=MagicMock())

db = SessionLocal()
db.add(Task(id="hot", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={"title": "Boho Sunset Print", "description": "boho"}, metadata_={}))
db.commit(); db.close()
# two stats events a day apart -> high velocity
from app.services.analytics_service import AnalyticsService
an = AnalyticsService()
an.record_event("listing_stats", "task", "hot", value=200, payload={"listing_id": "1", "views": 5, "favorites": 0})
time.sleep(0.01)
an.record_event("listing_stats", "task", "hot", value=400, payload={"listing_id": "1", "views": 60, "favorites": 3})
# make the two events a day apart
db = SessionLocal()
evs = db.query(AnalyticsEvent).filter(AnalyticsEvent.entity_id == "hot").order_by(AnalyticsEvent.created_at).all()
evs[0].created_at = datetime.utcnow() - timedelta(days=1)
db.commit(); db.close()

spawned = {}
with patch.object(settings, "AUTONOMY_ENABLED", True), \
     patch.object(settings, "ENGAGEMENT_VARIANT_MIN_VELOCITY", 10), \
     patch.object(worker, "_maybe_spawn_winner_variant", side_effect=lambda tid, source=None: spawned.update(tid=tid, source=source)):
    worker._maybe_engagement_variants()
check("3-10 high-velocity listing spawns an engagement variant", spawned.get("tid") == "hot")
check("3-10 variant tagged source=engagement_variant", spawned.get("source") == "engagement_variant")

# below threshold -> no spawn (fresh worker/state day)
worker2 = erw.EtsyReceiptWorker(fulfillment_service=MagicMock())
spawned2 = {}
with patch.object(settings, "AUTONOMY_ENABLED", True), \
     patch.object(settings, "ENGAGEMENT_VARIANT_MIN_VELOCITY", 100000), \
     patch.object(worker2, "_maybe_spawn_winner_variant", side_effect=lambda tid, source=None: spawned2.update(tid=tid)):
    worker2._maybe_engagement_variants()
check("3-10 below-threshold listing spawns nothing", "tid" not in spawned2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-106-F tests passed.")
