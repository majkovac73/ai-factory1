"""
Step 103 / TIER D test — trend cache (D-1), planner skip (D-2), alt text (D-4).

  [1] D-1: TrendDataService serves a fresh cache and misses on TTL / key change.
  [2] D-2: _plan for a product format produces a static one-step plan WITHOUT
      calling the PlannerAgent LLM.
  [3] D-4: attach_images_and_publish passes alt_text derived from the base + role.

Usage: python scripts/test_step103_tierd.py
"""
import asyncio
import os
import sys
import tempfile
import time
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "tierd.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] D-1 trend cache
from app.services.trend_data_service import TrendDataService
svc = TrendDataService.__new__(TrendDataService)  # skip pytrends init
payload = {"keywords": ["a", "b"], "rising_queries": {}, "interest_snapshot": {"a": 5}}
with patch.object(settings, "TREND_CACHE_HOURS", 12):
    svc._save_cache(payload)
    check("1 fresh cache hit for same keywords", svc._load_cache(["a", "b"]) == payload)
    check("1 miss for different keywords", svc._load_cache(["a", "c"]) is None)
with patch.object(settings, "TREND_CACHE_HOURS", 0):
    check("1 caching disabled -> miss", svc._load_cache(["a", "b"]) is None)

# [2] D-2 planner skip for product formats
from app.db.database import Base, engine
import app.models.task  # noqa
Base.metadata.create_all(bind=engine)
from app.services.task_service import TaskService
from app.schemas.task import TaskCreate
from app.services.task_processor import TaskProcessor

ts = TaskService()
task = ts.create_task(TaskCreate(prompt="make a coloring page of a fox", type="coloring_page"))
proc = TaskProcessor()
with patch("app.services.task_processor.PlannerAgent") as PlannerMock:
    proc._plan(task.id)
    check("2 PlannerAgent NOT constructed for product format", not PlannerMock.called)
reloaded = ts.get_task(task.id)
steps = (reloaded.metadata_ or {}).get("plan", {}).get("steps")
check("2 static one-step plan saved", steps == ["make a coloring page of a fox"])

# non-product type still uses the planner
task2 = ts.create_task(TaskCreate(prompt="write something", type="general"))
with patch("app.services.task_processor.PlannerAgent") as PlannerMock2:
    PlannerMock2.return_value.create_plan.return_value = {"steps": ["s1", "s2"]}
    proc._plan(task2.id)
    check("2 PlannerAgent used for non-product type", PlannerMock2.return_value.create_plan.called)

# [3] D-4 alt text
import app.services.etsy_image_service as eis
svc2 = eis.EtsyImageService()
captured = []
async def _fake_upload(listing_id, image_path, alt_text=None):
    captured.append((image_path, alt_text))
    return {"ok": True}
async def _fake_publish(listing_id):
    return {"published": True}
with patch.object(svc2, "upload_listing_image", new=_fake_upload), \
     patch.object(svc2, "publish_listing", new=_fake_publish):
    asyncio.run(svc2.attach_images_and_publish("L1", ["/x/hero.png", "/x/lifestyle.png"],
                                               alt_text_base="Boho Sunset Print"))
alts = {p.split("/")[-1]: a for p, a in captured}
check("3 hero alt derived from base+role", alts.get("hero.png") == "Boho Sunset Print — hero")
check("3 lifestyle alt derived", alts.get("lifestyle.png") == "Boho Sunset Print — lifestyle")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 TIER-D tests passed.")
