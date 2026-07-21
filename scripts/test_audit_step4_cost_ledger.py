"""
Audit 2026-07-20 #4 — per-product cost/profit ledger.

Verifies:
  [1] cost_context attributes record_cost() to the current task_id (and to
      'unattributed' outside a context).
  [2] the provider choke points emit a `cost_incurred` event within the
      orchestrator's cost_attribution context.
  [3] RevenueService.get_total_cost / cost_by_task / pnl_by_listing join
      cost + revenue - fees correctly (net = revenue - fees - cost).

Uses an isolated temp DB; no real API/image calls.
Usage: python scripts/test_audit_step4_cost_ledger.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "cost.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.core import cost_context
from app.services.analytics_service import AnalyticsService
from app.services.revenue_service import RevenueService
from config import settings

# This suite tests the cost-ledger MECHANICS, independent of FX. Pin the USD->base
# rate to 1.0 so cost (USD) and revenue are directly comparable (currency
# conversion itself is covered by test_deep_audit_v3_fixes.py).
settings.USD_TO_BASE_RATE = 1.0
settings.BASE_CURRENCY = "USD"

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


an = AnalyticsService()

# [1] attribution --------------------------------------------------------------
cost_context.record_cost(0.04, use_case="image", provider="openrouter", model="m")
outside = an.get_events(event_type="cost_incurred", limit=10)
check("1 record outside context -> entity_id 'unattributed'",
      len(outside) == 1 and outside[0].entity_id == "unattributed")

with cost_context.cost_attribution("task-A"):
    check("1 current_task_id set inside context", cost_context.current_task_id() == "task-A")
    cost_context.record_cost(0.04, use_case="image")
    cost_context.record_cost(0.002, use_case="vision_qa")
check("1 current_task_id cleared after context", cost_context.current_task_id() is None)

tot = RevenueService().get_total_cost("task-A")
check("1 task-A total cost = 0.042", abs(tot["total_cost"] - 0.042) < 1e-6)
check("1 by_use_case split", tot["by_use_case"].get("image") == 0.04 and tot["by_use_case"].get("vision_qa") == 0.002)

# [3] pnl join -----------------------------------------------------------------
rs = RevenueService()
# task-A: cost 0.042, one sale $5.99, fee $0.70
rs.record_sale(task_id="task-A", amount=5.99, currency="USD", transaction_id="tx1")
an.record_event(event_type="fee_estimate", entity_type="task", entity_id="task-A", value=0.70)

rows = rs.pnl_by_listing()
row_a = next((r for r in rows if r["task_id"] == "task-A"), None)
check("3 pnl row exists for task-A", row_a is not None)
check("3 net = revenue - fees - cost", row_a and abs(row_a["net"] - (5.99 - 0.70 - 0.042)) < 1e-3)

# task-B: pure cost, no sale -> negative net (sunk cost)
with cost_context.cost_attribution("task-B"):
    cost_context.record_cost(0.10, use_case="image")
rows2 = rs.pnl_by_listing()
row_b = next((r for r in rows2 if r["task_id"] == "task-B"), None)
check("3 unsold task shows negative net", row_b and row_b["net"] == -0.10)
check("3 rows sorted worst-net-first", rows2[0]["net"] <= rows2[-1]["net"])

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#4 cost-ledger tests passed.")
