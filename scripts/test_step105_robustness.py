"""
Step 105-F test — 5-1 resume dupes, 5-2 atomic state, 5-3 breaker except,
5-4 pin/video source filter, 5-5 frontend key wiring.

Usage: python scripts/test_step105_robustness.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from unittest.mock import MagicMock, patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s105f.db")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 5-1: resume with an existing listing_id does NOT re-create ──
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.task_service import TaskService

db = SessionLocal()
db.add(Task(id="resumed", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={"title": "Boho Print", "description": "boho", "listing_id": "555"},
            metadata_={}))
db.commit()
db.close()

orch = PipelineOrchestrator()
create_called = {"n": 0}


def fake_create(*a, **k):
    create_called["n"] += 1
    return "999"


attach_called = {"lid": None}
with patch.object(orch, "_stage_create_listing", side_effect=fake_create), \
     patch.object(orch, "_stage_attach_publish", side_effect=lambda tid, lid, *a, **k: attach_called.__setitem__("lid", lid)), \
     patch.object(orch, "_stage_pinterest"), patch.object(orch, "_stage_tumblr"), \
     patch.object(orch, "_stage_listing_images", return_value=[]), \
     patch.object(orch, "_stage_pod_design", return_value=None), \
     patch.object(orch, "_delivery_gate_error", return_value=None), \
     patch.object(orch, "_build_listing_mockups", return_value=[]), \
     patch.object(orch, "_stage_content_quality", side_effect=lambda *a, **k: a[1]):
    # give it a delivery asset so it proceeds to the create/attach branch
    orch.catalog = MagicMock()
    rep = orch.run_post_completion("resumed")

check("5-1 existing listing_id -> create_draft NOT called", create_called["n"] == 0)
check("5-1 resume attaches against the existing listing", attach_called["lid"] == "555")
check("5-1 report marks the listing reused",
      rep["stages"].get("create_listing", {}).get("reused") is not None)

# record_created_listing stamps output_data
TaskService().record_created_listing("resumed", "777")  # already has 555, must not overwrite
db = SessionLocal()
t = db.query(Task).filter(Task.id == "resumed").first()
check("5-1 record_created_listing does not overwrite an existing id", (t.output_data or {}).get("listing_id") == "555")
db.close()
db = SessionLocal()
db.add(Task(id="fresh", prompt="p", type="single_print", status="DONE", input_data={}, output_data={"title": "x"}, metadata_={}))
db.commit(); db.close()
TaskService().record_created_listing("fresh", "abc")
db = SessionLocal()
check("5-1 record_created_listing stamps a fresh task", (db.query(Task).filter(Task.id == "fresh").first().output_data or {}).get("listing_id") == "abc")
db.close()

# ── 5-2: receipt-worker _save_state is atomic (no leftover temp; readable) ──
import app.workers.etsy_receipt_worker as erw
w = erw.EtsyReceiptWorker(fulfillment_service=MagicMock())
w._save_state({"last_checked_at": 123, "failed_receipts": {"r1": 2}})
loaded = w._load_state()
check("5-2 state round-trips", loaded.get("last_checked_at") == 123 and loaded.get("failed_receipts") == {"r1": 2})
leftovers = [p for p in erw.STATE_FILE.parent.glob(f"{erw.STATE_FILE.name}.tmp*")]
check("5-2 no leftover temp state files", len(leftovers) == 0)

# ── 5-3: breaker check swallows non-SpendCap errors, raises SpendCapExceeded ──
from app.core.providers.openrouter_image_provider import OpenRouterImageProvider
from app.services.autonomy_service import SpendCapExceeded
prov = OpenRouterImageProvider()

# OSError from the ledger must NOT kill generation (it should proceed past the check)
reached = {"past": False}


class _Auto1:
    def assert_within_circuit_breaker(self):
        raise OSError("disk full")


with patch("app.services.autonomy_service.AutonomyService", _Auto1), \
     patch("httpx.AsyncClient") as HC:
    # make the HTTP call raise a sentinel AFTER the breaker check so we know we got past it
    HC.side_effect = RuntimeError("REACHED_HTTP")
    try:
        asyncio.run(prov.generate_image("x"))
    except RuntimeError as e:
        reached["past"] = "REACHED_HTTP" in str(e)
    except SpendCapExceeded:
        reached["past"] = False
check("5-3 non-SpendCap ledger error is swallowed (generation proceeds)", reached["past"])

# SpendCapExceeded must propagate
class _Auto2:
    def assert_within_circuit_breaker(self):
        raise SpendCapExceeded("over ceiling")


blocked = False
with patch("app.services.autonomy_service.AutonomyService", _Auto2):
    try:
        asyncio.run(prov.generate_image("x"))
    except SpendCapExceeded:
        blocked = True
    except Exception:
        blocked = False
check("5-3 SpendCapExceeded still propagates (refuses call)", blocked)

# ── 5-4: pin/video source excludes derived assets, filters use_case ──
from pathlib import Path
tmp = tempfile.mkdtemp()
from PIL import Image
photo = os.path.join(tmp, "hero.png"); Image.new("RGB", (64, 64), (1, 2, 3)).save(photo)
pinp = os.path.join(tmp, "pin.png"); Image.new("RGB", (64, 64), (4, 5, 6)).save(pinp)


def A(path, use_case):
    return type("A", (), {"local_path": path, "use_case": use_case})()


orch2 = PipelineOrchestrator()
orch2.catalog = MagicMock()
# pin.png first (as a re-run would have it), then the real listing photo
orch2.catalog.get_listing_assets.return_value = [A(pinp, "pinterest"), A(photo, "listing")]
src = orch2._mockup_source("t")
check("5-4 _mockup_source skips pin.png and pinterest use_case", src == photo)

orch2.catalog.get_listing_assets.return_value = [A(pinp, "listing")]  # even if mislabeled listing
check("5-4 _mockup_source excludes pin.png by filename", orch2._mockup_source("t") is None)

# ── 5-5: frontend attaches X-Factory-Key from localStorage on every fetch ──
html = open("frontend/index.html", encoding="utf-8").read()
check("5-5 apiFetch helper defined", "const apiFetch" in html and "X-Factory-Key" in html)
check("5-5 uses localStorage for the key", "localStorage" in html and "factoryApiKey" in html)
check("5-5 existing fetches routed through apiFetch",
      "apiFetch(`${API}/dashboard/rooms/status`)" in html and "apiFetch(`${API}/dashboard/pnl`)" in html)
check("5-5 key input rendered (KeyField)", "function KeyField" in html and "<KeyField" in html)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-105-F tests passed.")
