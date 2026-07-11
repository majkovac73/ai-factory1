"""
Step 104-E test — 2-3 variant guardrails, 2-4 title n-gram tags, 3-3 pin reuse,
4-2 charm pricing.

Usage: python scripts/test_step104_variant_tags_pin_charm.py
"""
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "e.db")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

# ── 4-2 charm pricing ──
from app.core.product_formats import snap_charm
check("4-2 5.75 -> 5.99", snap_charm(5.75, "single_print") == 5.99)
check("4-2 band cap 8.00 -> 7.99", snap_charm(8.00, "single_print") == 7.99)
check("4-2 all endings are .99/.49", str(round(snap_charm(4.10, "single_print") % 1, 2)) in ("0.99", "0.49"))

# ── 2-4 title n-grams (trademark-filtered) ──
from app.agents.etsy.listing_generator import ListingGeneratorAgent
ng = ListingGeneratorAgent.title_ngrams(["Minimalist Boho Wall Art Print", "Nike Swoosh Poster"])
check("2-4 mines 2-3 word phrases", "boho wall art" in ng or "minimalist boho wall" in ng)
check("2-4 excludes trademarked n-grams", all("nike" not in n for n in ng))
# _derive_tags uses extra_terms
tags = ListingGeneratorAgent()._derive_tags(["boho decor"], product_name="Sunset Print", extra_terms=["desert sunset art"])
check("2-4 extra_terms reach the tags", "desert sunset art" in tags)

# ── 2-3 winner-variant guardrails ──
db = SessionLocal()
db.add(Task(id="parent", prompt="p", type="pdf_planner_or_guide", status="DONE", input_data={},
            output_data={"title": "Weekly Planner"},
            metadata_={"market": {"top_titles": ["a"]}, "seo_context": ["b"], "page_count": 5}))
db.commit(); db.close()

import app.workers.etsy_receipt_worker as erw
worker = erw.EtsyReceiptWorker(fulfillment_service=MagicMock())

# spend cap reached -> no variant
with patch.object(settings, "AUTONOMY_ENABLED", True), patch.object(settings, "WINNER_VARIANTS_PER_DAY", 2):
    auto = MagicMock()
    auto.can_create_winner_variant.return_value = True
    auto.can_spend.return_value = False  # cap reached
    with patch("app.services.autonomy_service.AutonomyService", return_value=auto), \
         patch("app.services.task_service.TaskService") as TS:
        worker._maybe_spawn_winner_variant("parent")
    check("2-3 no variant when can_spend False", not TS.return_value.create_task.called)

# spend ok -> variant carries parent grounding
with patch.object(settings, "AUTONOMY_ENABLED", True), patch.object(settings, "WINNER_VARIANTS_PER_DAY", 2):
    auto2 = MagicMock()
    auto2.can_create_winner_variant.return_value = True
    auto2.can_spend.return_value = True
    created = {}
    ts = MagicMock()
    ts.get_task.return_value = SessionLocal().query  # placeholder, replaced below
    from app.services.task_service import TaskService as RealTS
    real_ts = RealTS()
    def _capture(tc):
        created["metadata"] = tc.metadata
        return MagicMock(id="v1")
    with patch("app.services.autonomy_service.AutonomyService", return_value=auto2), \
         patch("app.services.task_service.TaskService") as TS2:
        TS2.return_value.get_task.return_value = real_ts.get_task("parent")
        TS2.return_value.create_task.side_effect = _capture
        worker._maybe_spawn_winner_variant("parent")
    md = created.get("metadata") or {}
    check("2-3 variant carries page_count from parent", md.get("page_count") == 5)
    check("2-3 variant carries market from parent", "market" in md)
    check("2-3 variant carries seo_context from parent", "seo_context" in md)

# ── 3-3 pin from mockup (no image generation) ──
from app.services.pipeline_orchestrator import PipelineOrchestrator
from PIL import Image
orch = PipelineOrchestrator()
# fake a listing mockup on disk
mp = os.path.join(tempfile.mkdtemp(), "hero.png")
Image.new("RGB", (1024, 1024), (200, 180, 160)).save(mp)
orch.catalog = MagicMock()
orch.catalog.get_listing_assets.return_value = [type("A", (), {"local_path": mp})()]
pin = orch._pin_from_mockup("task-x")
check("3-3 pin built from mockup (no generation)", pin is not None and os.path.exists(pin))
check("3-3 pin is 2:3 (1000x1500)", Image.open(pin).size == (1000, 1500))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-E tests passed.")
