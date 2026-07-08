"""
One-off — retroactively mark task 97f0e7a0 as BLOCKED_NO_PRODUCT.

This task ("Eco-Friendly Digital Downloads" / "A collection of digital
downloads and print-on-demand products...") is the second bad listing:
created 2026-07-08T05:08:37Z during a real autonomy cycle
(AUTONOMY_ENABLED=True at the time), 29 minutes before the product-gate fix
was committed. It predates the fix entirely and was never blocked, so its
output_data has no pipeline_status field reflecting reality.

Does NOT touch task.status (DONE) or delete anything — only merges two keys
into output_data, the same field PipelineOrchestrator.record_pipeline_block()
writes for tasks the gate blocks going forward. Maj is deleting the actual
Etsy draft listing manually; this only makes the historical task record
consistent with that.

Same safety pattern as cleanup_test_data.py: print what will change, require
explicit confirmation, then apply it.

Run against Railway (inside the deployed container, via railway ssh — NOT
`railway run`, which executes locally and cannot see the volume-mounted
/data/app.db):
  railway ssh -- "python3 scripts/fix_task_97f0e7a0.py"
  railway ssh -- "python3 scripts/fix_task_97f0e7a0.py --yes"   # apply
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal
from app.models.task import Task

TASK_ID = "97f0e7a0-deb2-4982-adb7-8e1179e15647"
REASON = (
    "Pre-fix bad listing: created 2026-07-08T05:08:37Z during a real "
    "autonomy cycle before the product-before-listing gate existed "
    "(gate fix committed 2026-07-08T05:37:33Z). No real delivery asset "
    "was ever verified for this task; the concept itself was a vague "
    "category ('a collection of digital downloads and print-on-demand "
    "products'), not a specific product. Retroactively marked — the "
    "real Etsy draft listing is being deleted manually."
)

db = SessionLocal()
try:
    task = db.query(Task).filter(Task.id == TASK_ID).first()
    if not task:
        print(f"Task {TASK_ID} not found. Nothing to do.")
        sys.exit(1)

    current = dict(task.output_data or {})
    if current.get("pipeline_status") == "BLOCKED_NO_PRODUCT":
        print(f"Task {TASK_ID} is already marked BLOCKED_NO_PRODUCT. Nothing to do.")
        print(f"  pipeline_blocked_reason: {current.get('pipeline_blocked_reason')}")
        sys.exit(0)

    updated = dict(current)
    updated["pipeline_status"] = "BLOCKED_NO_PRODUCT"
    updated["pipeline_blocked_reason"] = REASON

    print(f"Task {TASK_ID} (status={task.status}, type={task.type})")
    print("Current output_data:")
    print(f"  {current}")
    print("\nWill update output_data to:")
    print(f"  {updated}")
    print("\ntask.status is NOT changed (stays DONE) — only output_data is merged.")

    if "--yes" not in sys.argv:
        print("\nDry run only. Re-run with --yes to apply this change.")
        sys.exit(0)

    task.output_data = updated
    db.commit()
    print(f"\nApplied. Task {TASK_ID} output_data updated.")
finally:
    db.close()
