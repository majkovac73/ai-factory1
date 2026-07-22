"""
POD shipping-profile guards: don't propose/generate POD when it can't be listed.

A POD (physical) Etsy listing 400s without a shipping_profile_id. Two guards:
  1. TrendResearchAgent._proposable_formats excludes pod_apparel_design unless
     POD_APPAREL_ENABLED AND ETSY_SHIPPING_PROFILE_ID is set.
  2. PipelineOrchestrator blocks a POD task BEFORE any generation when the
     shipping profile is missing (fail fast, no wasted spend).

Usage: python scripts/test_pod_shipping_guard.py
"""
import os, sys, tempfile
from unittest.mock import patch, MagicMock
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pod.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.agents.trend_research_agent import TrendResearchAgent
base = dict(WALL_ART_SET_ENABLED=False, SEAMLESS_PATTERN_ENABLED=False, PHONE_WALLPAPER_ENABLED=False)

def formats(**over):
    with patch.multiple(settings, **{**base, **over}):
        return TrendResearchAgent._proposable_formats()

check("1 POD off -> excluded",
      "pod_apparel_design" not in formats(POD_APPAREL_ENABLED=False, ETSY_SHIPPING_PROFILE_ID="s1"))
check("1 POD on but NO shipping profile -> excluded",
      "pod_apparel_design" not in formats(POD_APPAREL_ENABLED=True, ETSY_SHIPPING_PROFILE_ID=None))
check("1 POD on WITH shipping profile -> included",
      "pod_apparel_design" in formats(POD_APPAREL_ENABLED=True, ETSY_SHIPPING_PROFILE_ID="s1"))

# 2 orchestrator fast-fail
from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.services.pipeline_orchestrator import PipelineOrchestrator
Base.metadata.create_all(bind=engine)
db = SessionLocal()
db.add(Task(id="pod-task", prompt="tee", type="pod_apparel_design", status="DONE",
            output_data={"title": "Vintage Tee"}))
db.commit(); db.close()

orch = PipelineOrchestrator()
with patch.object(settings, "ETSY_SHIPPING_PROFILE_ID", None), \
     patch.object(orch, "_block_task", wraps=orch._block_task) as blk, \
     patch("app.services.task_service.TaskService.record_pipeline_block", MagicMock()), \
     patch.object(orch, "_alert", MagicMock()):
    rep = orch.run_post_completion("pod-task")
check("2 POD task blocked before generation (no shipping profile)", rep.get("blocked") is True)
check("2 block reason names the shipping profile", "SHIPPING_PROFILE" in str(rep.get("blocked_reason", "")).upper() or "shipping profile" in str(rep.get("blocked_reason","")).lower())
check("2 no listing_images stage ran (failed fast)", "listing_images" not in rep.get("stages", {}))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All POD shipping-guard tests passed.")
