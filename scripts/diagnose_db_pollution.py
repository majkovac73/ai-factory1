"""
Diagnostic — print all rows relevant to test pollution in the Railway DB.

Run against Railway:
  railway run python scripts/diagnose_db_pollution.py

Covers:
  - FulfillmentRecord: all rows + STRESS-RECEIPT-* breakdown
  - PODProduct: all rows, flagged by known test listing_id patterns
  - Task: all rows, flagged by known test prompt patterns
  - ImageAsset: count by task_id category

Produces a clean/dirty verdict at the end.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.db.database import SessionLocal
from app.models.fulfillment_record import FulfillmentRecord
from app.models.pod_product import PODProduct
from app.models.task import Task
from app.models.image_asset import ImageAsset

# Known test artifact patterns (mirrors cleanup_test_data.py)
LISTING_IDS_STEP81 = {"999000111", "999000222"}
LISTING_IDS_STEP83 = {str(9000000 + i) for i in range(10)}
LISTING_IDS_STEP84 = {"LIST-PERF-001"}
ALL_TEST_LISTING_IDS = LISTING_IDS_STEP81 | LISTING_IDS_STEP83 | LISTING_IDS_STEP84

STRESS_RECEIPT_PREFIX = "STRESS-RECEIPT-"
TASK_PROMPT_LIKE_STRESS     = "Stress test task %"
TASK_PROMPT_LIKE_CONCURRENT = "Concurrent write %"

db = SessionLocal()
try:
    # ── FulfillmentRecord ──────────────────────────────────────────────────────
    records = (
        db.query(FulfillmentRecord)
        .order_by(FulfillmentRecord.created_at)
        .all()
    )
    stress_fr  = [r for r in records if str(r.etsy_receipt_id).startswith(STRESS_RECEIPT_PREFIX)]
    real_fr    = [r for r in records if not str(r.etsy_receipt_id).startswith(STRESS_RECEIPT_PREFIX)]

    print(f"\n=== FulfillmentRecord: {len(records)} total ===\n")
    print(f"{'id[:8]':<10} {'etsy_receipt_id':<30} {'etsy_transaction_id':<25} {'status':<18} {'created_at'}")
    print("-" * 115)
    for r in records:
        tag = " [TEST]" if str(r.etsy_receipt_id).startswith(STRESS_RECEIPT_PREFIX) else ""
        print(
            f"{r.id[:8]:<10} {str(r.etsy_receipt_id):<30} "
            f"{str(r.etsy_transaction_id):<25} {r.status:<18} {r.created_at}{tag}"
        )
    print(f"\n  STRESS-RECEIPT-* (test artifacts) : {len(stress_fr)}")
    print(f"  Real receipt rows                  : {len(real_fr)}")

    # ── PODProduct ─────────────────────────────────────────────────────────────
    pods = db.query(PODProduct).order_by(PODProduct.created_at).all()
    test_pods = [p for p in pods if str(p.etsy_listing_id or "") in ALL_TEST_LISTING_IDS]
    real_pods = [p for p in pods if str(p.etsy_listing_id or "") not in ALL_TEST_LISTING_IDS]

    print(f"\n=== PODProduct: {len(pods)} total ===\n")
    print(f"{'id[:8]':<10} {'etsy_listing_id':<20} {'task_id[:8]':<12} {'created_at':<25} tag")
    print("-" * 80)
    for p in pods:
        lid = str(p.etsy_listing_id or "")
        tag = (
            "[TEST-step81]" if lid in LISTING_IDS_STEP81 else
            "[TEST-step83]" if lid in LISTING_IDS_STEP83 else
            "[TEST-step84]" if lid in LISTING_IDS_STEP84 else
            ""
        )
        print(
            f"{p.id[:8]:<10} {lid:<20} {str(p.task_id or '')[:8]:<12} "
            f"{str(p.created_at):<25} {tag}"
        )
    print(f"\n  Test PODProducts (known patterns) : {len(test_pods)}")
    print(f"    step81 (999000111/222)           : {sum(1 for p in test_pods if str(p.etsy_listing_id) in LISTING_IDS_STEP81)}")
    print(f"    step83 (9000000-9000009)         : {sum(1 for p in test_pods if str(p.etsy_listing_id) in LISTING_IDS_STEP83)}")
    print(f"    step84 (LIST-PERF-001)           : {sum(1 for p in test_pods if str(p.etsy_listing_id) in LISTING_IDS_STEP84)}")
    print(f"  Real PODProducts                  : {len(real_pods)}")

    # ── Task ───────────────────────────────────────────────────────────────────
    all_tasks = db.query(Task).order_by(Task.created_at).all()
    tasks_stress      = [t for t in all_tasks if (t.prompt or "").startswith("Stress test task")]
    tasks_concurrent  = [t for t in all_tasks if (t.prompt or "").startswith("Concurrent write")]
    real_tasks        = [
        t for t in all_tasks
        if not (t.prompt or "").startswith("Stress test task")
        and not (t.prompt or "").startswith("Concurrent write")
    ]

    print(f"\n=== Task: {len(all_tasks)} total ===\n")
    print(f"{'id[:8]':<10} {'status':<12} {'created_at':<25} prompt[:60]")
    print("-" * 100)
    for t in all_tasks:
        prompt = str(t.prompt or "")
        tag = (
            " [TEST-step83]" if prompt.startswith("Stress test task") else
            " [TEST-step84]" if prompt.startswith("Concurrent write") else
            ""
        )
        print(f"{t.id[:8]:<10} {t.status:<12} {str(t.created_at):<25} {prompt[:60]}{tag}")

    print(f"\n  'Stress test task %' (step83 test) : {len(tasks_stress)}")
    print(f"  'Concurrent write %' (step84 test) : {len(tasks_concurrent)}")
    print(f"  Real tasks                          : {len(real_tasks)}")

    # ── ImageAsset ─────────────────────────────────────────────────────────────
    test_pod_task_ids   = {p.task_id for p in test_pods if p.task_id}
    test_direct_task_ids = {t.id for t in tasks_stress + tasks_concurrent}
    all_test_task_ids   = test_pod_task_ids | test_direct_task_ids

    total_assets = db.query(ImageAsset).count()
    test_assets  = (
        db.query(ImageAsset)
        .filter(ImageAsset.task_id.in_(list(all_test_task_ids)))
        .count()
    ) if all_test_task_ids else 0

    print(f"\n=== ImageAsset: {total_assets} total, {test_assets} tied to test task_ids ===")

    # ── Verdict ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    any_test_pollution = (
        len(stress_fr) > 0 or len(test_pods) > 0 or
        len(tasks_stress) > 0 or len(tasks_concurrent) > 0 or test_assets > 0
    )
    if any_test_pollution:
        print("VERDICT: test artifacts present — run cleanup_test_data.py")
        print(f"  FulfillmentRecord test rows : {len(stress_fr)}")
        print(f"  PODProduct test rows        : {len(test_pods)}")
        print(f"  Task test rows              : {len(tasks_stress) + len(tasks_concurrent)}")
        print(f"  ImageAsset test rows        : {test_assets}")
    else:
        print("VERDICT: clean — no known test artifacts found")
    print("=" * 60 + "\n")

finally:
    db.close()
