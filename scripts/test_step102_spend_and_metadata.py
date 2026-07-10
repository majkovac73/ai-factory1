"""
Step 102 / P0-13 + P3-8 test.

P0-13 (honest spend ledger):
  [1] the image-provider choke point records IMAGE_COST_USD per image.
  [2] each vision-QA call records VISION_QA_COST_USD.

P3-8 (save_plan no longer clobbers autonomy metadata):
  [3] save_plan MERGES the plan under metadata_["plan"], preserving
      source=autonomy_worker and page_count.
  [4] the executor reads steps from metadata_["plan"] (new layout) and still
      falls back to a legacy flat blob.

Usage: python scripts/test_step102_spend_and_metadata.py
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "app.db")  # also isolates the autonomy ledger dir

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.models.task import Task  # noqa: F401
from config import settings
from app.services.autonomy_service import AutonomyService
from app.core.providers.openrouter_image_provider import OpenRouterImageProvider
from app.services.content_quality_service import ContentQualityService
from app.services.task_service import TaskService
from app.schemas.task import TaskCreate

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)


def spend():
    return AutonomyService().daily_status()["spend_usd"]


# [1] image choke point
before = spend()
OpenRouterImageProvider._record_image_spend()
check("1 image spend recorded (+IMAGE_COST_USD)", abs(spend() - before - settings.IMAGE_COST_USD) < 1e-9)

# 3 images -> 3x
before = spend()
for _ in range(3):
    OpenRouterImageProvider._record_image_spend()
check("1 three images -> 3x IMAGE_COST_USD", abs(spend() - before - 3 * settings.IMAGE_COST_USD) < 1e-9)

# [2] vision QA
before = spend()
ContentQualityService._charge_vision()
check("2 vision QA spend recorded (+VISION_QA_COST_USD)", abs(spend() - before - settings.VISION_QA_COST_USD) < 1e-9)

# [3] + [4] save_plan merge
ts = TaskService()
task = ts.create_task(TaskCreate(
    prompt="make a planner",
    type="pdf_planner_or_guide",
    metadata={"source": "autonomy_worker", "product_name": "Weekly Planner", "page_count": 5},
))
ts.save_plan(task.id, {"steps": ["step one", "step two"]})

reloaded = ts.get_task(task.id)
meta = reloaded.metadata_ or {}
check("3 source preserved after save_plan", meta.get("source") == "autonomy_worker")
check("3 page_count preserved after save_plan", meta.get("page_count") == 5)
check("3 plan stored under metadata_['plan']", meta.get("plan", {}).get("steps") == ["step one", "step two"])

# [4] executor-style read (new layout + legacy fallback)
plan_new = meta.get("plan", meta)
check("4 executor reads steps from new layout", plan_new.get("steps") == ["step one", "step two"])
legacy = {"steps": ["legacy"]}
plan_legacy = legacy.get("plan", legacy)
check("4 executor falls back to legacy flat blob", plan_legacy.get("steps") == ["legacy"])

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 spend+metadata tests passed.")
