"""
Marketing-refresh tests — doubles only, zero real API calls, zero generation.

Confirms:
  [1] Candidates are the published products (DONE + recognized format + a real
      persisted etsy_listing_id) whose last successful marketing post (any
      channel) is null or older than MARKETING_REFRESH_MIN_INTERVAL_DAYS, sorted
      LEAST-RECENTLY-MARKETED first (never-marketed before older-than before
      more-recent).
  [2] A product marketed within MARKETING_REFRESH_MIN_INTERVAL_DAYS is excluded;
      a DONE task with no real listing_id (never published / was blocked) is
      excluded.
  [3] MARKETING_REFRESH_MAX_POSTS_PER_CYCLE is respected — one cycle posts at
      most that many, using existing assets, and records a MarketingPost each.
  [4] MARKETING_REFRESH_ENABLED=False keeps the worker completely inert
      (kill-switch pattern, same as AutonomyWorker).

Usage:
  python scripts/test_marketing_refresh.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".refresh.db", delete=False)
_tmp.close()
os.environ.pop("DATABASE_PATH", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
_img_root = tempfile.mkdtemp()
os.environ["IMAGE_STORAGE_ROOT"] = _img_root

import logging
logging.basicConfig(level=logging.ERROR)

from PIL import Image as PILImage

from app.db.database import Base, engine, SessionLocal
import app.models.task, app.models.log, app.models.analytics_event
import app.models.image_asset, app.models.marketing_post, app.models.pod_product
Base.metadata.create_all(bind=engine)

from app.models.task import Task
from app.models.marketing_post import MarketingPost
from app.schemas.enums import TaskStatus
from app.services.image_catalog_service import ImageCatalogService
from app.services.marketing_refresh_service import MarketingRefreshService
from config import settings

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nMarketing-refresh tests (doubles only)\n")

NOW = datetime.utcnow()


class FakeChannel:
    name = "tumblr"

    def __init__(self):
        self.calls = []

    def post(self, listing: dict) -> dict:
        self.calls.append(listing)
        return {"success": True, "external_id": f"ext-{len(self.calls)}", "url": "https://productsforall.tumblr.com/post/1", "error": None}


def _make_png(task_id: str) -> str:
    d = Path(_img_root) / "listing" / task_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "hero.png"
    PILImage.new("RGB", (1024, 1024), (100, 140, 200)).save(p, format="PNG")
    return str(p)


def _make_task(task_id: str, created_at: datetime, published: bool = True, marketed_days_ago=None):
    """Insert a DONE single_print task. If published, register a listing asset
    with a real listing_id. If marketed_days_ago, add a successful MarketingPost."""
    db = SessionLocal()
    try:
        t = Task(
            id=task_id,
            prompt=f"Product {task_id}",
            type="single_print",
            status=TaskStatus.DONE.value,
            input_data={},
            output_data={"title": f"Title {task_id}", "description": f"Desc {task_id}", "keywords": ["a", "b"]},
            created_at=created_at,
        )
        db.add(t)
        db.commit()
    finally:
        db.close()

    if published:
        path = _make_png(task_id)
        ImageCatalogService().register(
            task_id=task_id, local_path=path, variant="listing", use_case="listing",
            agent="test", listing_id=f"L-{task_id}",
        )

    if marketed_days_ago is not None:
        db = SessionLocal()
        try:
            db.add(MarketingPost(
                task_id=task_id, channel="tumblr", status="success",
                created_at=NOW - timedelta(days=marketed_days_ago),
            ))
            db.commit()
        finally:
            db.close()


# Build catalog:
#  A: published, never marketed        -> candidate, sorts FIRST
#  B: published, marketed 30 days ago  -> candidate (older), sorts after A
#  E: published, marketed 10 days ago  -> candidate, sorts after B
#  C: published, marketed 2 days ago   -> EXCLUDED (within 7d default)
#  D: DONE but NOT published (no listing_id) -> EXCLUDED
_make_task("A", NOW - timedelta(days=100), published=True, marketed_days_ago=None)
_make_task("B", NOW - timedelta(days=90), published=True, marketed_days_ago=30)
_make_task("E", NOW - timedelta(days=80), published=True, marketed_days_ago=10)
_make_task("C", NOW - timedelta(days=70), published=True, marketed_days_ago=2)
_make_task("D", NOW - timedelta(days=60), published=False, marketed_days_ago=None)


# ── [1] candidates least-recently-marketed first ─────────────────────────────
print("[1] select_candidates returns published products oldest-marketed first...")

svc = MarketingRefreshService()
cands = svc.select_candidates(limit=10)
ids = [c.task_id for c in cands]
if ids == ["A", "B", "E"]:
    ok(f"[1] correct order + set: {ids}")
else:
    fail("[1] candidate selection", f"got {ids}, expected ['A','B','E']")


# ── [2] recent-post exclusion + unpublished exclusion ────────────────────────
print("[2] within-interval product (C) and unpublished task (D) are excluded...")

if "C" not in ids and "D" not in ids:
    ok("[2] C (marketed 2d ago) and D (no listing_id) both excluded")
else:
    fail("[2] exclusions", f"ids={ids}")


# ── [3] MAX_POSTS_PER_CYCLE respected ────────────────────────────────────────
print("[3] run_cycle respects the per-cycle cap and records a MarketingPost each...")

fake = FakeChannel()
db = SessionLocal()
try:
    before = db.query(MarketingPost).filter(MarketingPost.status == "success").count()
finally:
    db.close()

result = svc.run_cycle([fake], max_posts=2, rewrite_caption=False)

db = SessionLocal()
try:
    after = db.query(MarketingPost).filter(MarketingPost.status == "success").count()
finally:
    db.close()

# Posted exactly 2 (A and B, the two least-recently-marketed), each with a real
# existing asset path and the listing link, and each recorded.
posted_ok = result["posted"] == 2 and len(fake.calls) == 2
recorded_ok = (after - before) == 2
target_ok = {r["task_id"] for r in result["results"]} == {"A", "B"}
link_ok = all("etsy.com/listing/L-" in (c.get("listing_url") or "") for c in fake.calls)
asset_ok = all(Path(c.get("image_path")).exists() for c in fake.calls)
if posted_ok and recorded_ok and target_ok and link_ok and asset_ok:
    ok("[3] cap respected: 2 posts, existing assets, listing links, each recorded")
else:
    fail("[3] cap/posting", f"posted={result['posted']}, calls={len(fake.calls)}, recorded={after-before}, targets={[r['task_id'] for r in result['results']]}, link_ok={link_ok}, asset_ok={asset_ok}")


# ── [4] kill switch: worker inert when disabled ──────────────────────────────
print("[4] MARKETING_REFRESH_ENABLED=False keeps the worker inert...")

from app.workers.marketing_refresh_worker import MarketingRefreshWorker


class CountingService:
    def __init__(self):
        self.cycles = 0

    def run_cycle(self, channels, max_posts=None, rewrite_caption=True):
        self.cycles += 1
        return {"posted": 0, "results": []}


# Disabled → never calls run_cycle.
counter_off = CountingService()
worker_off = MarketingRefreshWorker(schedule_seconds=0.05, service=counter_off)
worker_off._available_channels = lambda: [FakeChannel()]
with patch.object(settings, "MARKETING_REFRESH_ENABLED", False):
    worker_off.start()
    time.sleep(0.3)
    worker_off.stop()

# Enabled → runs at least one cycle (positive control).
counter_on = CountingService()
worker_on = MarketingRefreshWorker(schedule_seconds=0.05, service=counter_on)
worker_on._available_channels = lambda: [FakeChannel()]
with patch.object(settings, "MARKETING_REFRESH_ENABLED", True):
    worker_on.start()
    deadline = time.time() + 2.0
    while counter_on.cycles == 0 and time.time() < deadline:
        time.sleep(0.05)
    worker_on.stop()

if counter_off.cycles == 0 and counter_on.cycles > 0:
    ok(f"[4] kill switch works: disabled=0 cycles, enabled={counter_on.cycles} cycle(s)")
else:
    fail("[4] kill switch", f"disabled={counter_off.cycles}, enabled={counter_on.cycles}")


print(f"\nResults: {_passed} passed, {_failed} failed\n")
try:
    os.unlink(_tmp.name)
except Exception:
    pass
sys.exit(0 if _failed == 0 else 1)
