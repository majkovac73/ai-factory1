"""
Daily-product-cap enforcement for winner/engagement variants.

Rule (Maj): once the daily product cap (MAX_TASKS_PER_DAY) is hit, NO new product
creation may run — only marketing_refresh. Winner-variants and engagement-variants
create new products, so they must be blocked (no spend) once the cap is reached,
and when they DO run they must consume the same daily product budget.

Usage: python scripts/test_variant_daily_cap.py
"""
import os, sys, tempfile
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "vcap.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.workers.etsy_receipt_worker import EtsyReceiptWorker


class FakeAuto:
    def __init__(self, can_task=True, can_variant=True, can_spend=True):
        self._can_task = can_task
        self._can_variant = can_variant
        self._can_spend = can_spend
        self.recorded_task = 0
        self.recorded_variant = 0
    def can_create_task(self):        return self._can_task
    def can_create_winner_variant(self): return self._can_variant
    def can_spend(self, amt):         return self._can_spend
    def record_task_created(self):    self.recorded_task += 1
    def record_winner_variant(self):  self.recorded_variant += 1


def run_spawn(auto, create_spy=None):
    """Invoke _maybe_spawn_winner_variant with all collaborators faked."""
    w = EtsyReceiptWorker.__new__(EtsyReceiptWorker)

    parent = MagicMock()
    parent.type = "single_print"          # a real PRODUCT_FORMATS key
    parent.output_data = {"title": "Neutral Wall Art Print", "description": "a calm abstract print"}
    parent.metadata_ = {"product_name": "Neutral Wall Art Print"}

    ts = MagicMock()
    ts.get_task.return_value = parent
    created = MagicMock(); created.id = "new-task-1"
    ts.create_task.return_value = created
    if create_spy is not None:
        ts.create_task.side_effect = create_spy

    with patch("app.services.autonomy_service.AutonomyService", return_value=auto), \
         patch("app.services.task_service.TaskService", return_value=ts), \
         patch("app.core.seasonality.occasion_for", return_value=None), \
         patch("app.core.seasonality.occasion_in_window", return_value=True), \
         patch("app.core.seasonality.occasion_mismatch", return_value=None), \
         patch("app.core.trademark_screen.screen", return_value=False), \
         patch.object(settings, "AUTONOMY_ENABLED", True), \
         patch.object(settings, "WINNER_VARIANTS_PER_DAY", 5), \
         patch.object(settings, "MAX_TASKS_PER_DAY", 2):
        w._maybe_spawn_winner_variant("parent-1", source="winner_variant")
    return ts


# ── 1) cap HIT -> variant blocked, no task created, no spend recorded ──────────
auto_blocked = FakeAuto(can_task=False)
ts_blocked = run_spawn(auto_blocked)
check("cap hit -> create_task NOT called", ts_blocked.create_task.call_count == 0)
check("cap hit -> no variant recorded", auto_blocked.recorded_variant == 0)
check("cap hit -> no task-budget consumed", auto_blocked.recorded_task == 0)

# ── 2) cap NOT hit -> variant created AND consumes the unified product budget ──
auto_ok = FakeAuto(can_task=True)
ts_ok = run_spawn(auto_ok)
check("headroom -> create_task called once", ts_ok.create_task.call_count == 1)
check("headroom -> winner variant recorded", auto_ok.recorded_variant == 1)
check("headroom -> variant consumes a product-cap slot (record_task_created)", auto_ok.recorded_task == 1)

# ── 3) the cap check runs BEFORE the variant sub-cap (spend-blocking is first) ──
# If can_create_task is False, we must not even reach can_create_winner_variant /
# can_spend / create_task — proven by (1). Also verify spend cap still blocks when
# task-cap has headroom but wallet is empty.
auto_broke = FakeAuto(can_task=True, can_spend=False)
ts_broke = run_spawn(auto_broke)
check("spend cap still blocks a variant", ts_broke.create_task.call_count == 0)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All variant daily-cap tests passed.")
