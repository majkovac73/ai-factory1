"""
Cleanup — remove ALL test-created rows from the production Railway DB.

Known test artifact patterns:
  FulfillmentRecord:
    - etsy_receipt_id LIKE 'STRESS-RECEIPT-%'      (test_step83)
    - pod_product_id in test PODProduct set

  PODProduct:
    - etsy_listing_id in (999000111, 999000222)    (test_step81)
    - etsy_listing_id in (9000000..9000009)        (test_step83)
    - etsy_listing_id = 'LIST-PERF-001'            (test_step84)

  Task:
    - prompt LIKE 'Stress test task %'             (test_step83)
    - prompt LIKE 'Concurrent write %'             (test_step84)

  ImageAsset:
    - task_id belonging to any test PODProduct or test Task

Run against Railway:
  railway run python scripts/cleanup_test_data.py

Does NOT touch:
  - Real Etsy receipt FulfillmentRecords
  - Real tasks / listings
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

# ── Identify all test artifact patterns ───────────────────────────────────────

# PODProduct listing IDs produced by each test script
LISTING_IDS_STEP81  = {"999000111", "999000222"}
LISTING_IDS_STEP83  = {str(9000000 + i) for i in range(10)}   # 9000000-9000009
LISTING_IDS_STEP84  = {"LIST-PERF-001"}
ALL_TEST_LISTING_IDS = LISTING_IDS_STEP81 | LISTING_IDS_STEP83 | LISTING_IDS_STEP84

# Task prompt patterns produced by each test script
TASK_PROMPT_LIKE_STRESS      = "Stress test task %"    # test_step83
TASK_PROMPT_LIKE_CONCURRENT  = "Concurrent write %"    # test_step84

STRESS_RECEIPT_PREFIX = "STRESS-RECEIPT-"

db = SessionLocal()
try:
    # ── 1. Find test PODProduct rows ───────────────────────────────────────────
    test_pods = (
        db.query(PODProduct)
        .filter(PODProduct.etsy_listing_id.in_(list(ALL_TEST_LISTING_IDS)))
        .order_by(PODProduct.created_at)
        .all()
    )
    test_pod_ids  = {p.id for p in test_pods}
    test_task_ids_from_pods = {p.task_id for p in test_pods if p.task_id}

    print(f"Test PODProduct rows found: {len(test_pods)}")
    for p in test_pods:
        src = (
            "step81" if p.etsy_listing_id in LISTING_IDS_STEP81 else
            "step83" if p.etsy_listing_id in LISTING_IDS_STEP83 else
            "step84"
        )
        print(f"  [{src}] {p.id[:8]}  listing_id={p.etsy_listing_id:<15}  "
              f"task_id={str(p.task_id or '')[:8]}  created_at={p.created_at}")

    # ── 2. FulfillmentRecords tied to test PODProducts ─────────────────────────
    fr_by_pod = (
        db.query(FulfillmentRecord)
        .filter(FulfillmentRecord.pod_product_id.in_(list(test_pod_ids)))
        .all()
    ) if test_pod_ids else []

    # FulfillmentRecords from stress test receipts
    fr_stress = (
        db.query(FulfillmentRecord)
        .filter(FulfillmentRecord.etsy_receipt_id.like(f"{STRESS_RECEIPT_PREFIX}%"))
        .all()
    )

    all_fr = list({r.id: r for r in list(fr_by_pod) + list(fr_stress)}.values())
    print(f"\nFulfillmentRecord rows to delete: {len(all_fr)}")
    for r in all_fr:
        print(f"  {r.id[:8]}  receipt={r.etsy_receipt_id}  txn={r.etsy_transaction_id}  status={r.status}")

    # ── 3. Task rows from test scripts ─────────────────────────────────────────
    test_tasks_stress = (
        db.query(Task)
        .filter(Task.prompt.like(TASK_PROMPT_LIKE_STRESS))
        .all()
    )
    test_tasks_concurrent = (
        db.query(Task)
        .filter(Task.prompt.like(TASK_PROMPT_LIKE_CONCURRENT))
        .all()
    )
    all_test_tasks = test_tasks_stress + test_tasks_concurrent
    test_task_ids_direct = {t.id for t in all_test_tasks}

    print(f"\nTask rows to delete: {len(all_test_tasks)}")
    for t in all_test_tasks:
        src = "step83" if t.prompt.startswith("Stress test task") else "step84"
        print(f"  [{src}] {t.id[:8]}  status={t.status:<10}  prompt={str(t.prompt or '')[:50]}")

    # ── 4. ImageAsset rows tied to test task_ids ───────────────────────────────
    all_test_task_ids = test_task_ids_from_pods | test_task_ids_direct
    test_image_assets = (
        db.query(ImageAsset)
        .filter(ImageAsset.task_id.in_(list(all_test_task_ids)))
        .all()
    ) if all_test_task_ids else []

    print(f"\nImageAsset rows to delete: {len(test_image_assets)}")
    for a in test_image_assets:
        print(f"  {a.id[:8]}  task_id={str(a.task_id or '')[:8]}  variant={a.variant}  "
              f"path={str(a.local_path or '')[:50]}")

    # ── Summary and confirmation ───────────────────────────────────────────────
    total = len(all_fr) + len(test_pods) + len(all_test_tasks) + len(test_image_assets)

    if total == 0:
        print("\nNothing to clean up. DB is already clean.")
        sys.exit(0)

    print(f"\nAbout to delete:")
    print(f"  {len(all_fr)} FulfillmentRecord rows")
    print(f"  {len(test_pods)} PODProduct rows")
    print(f"  {len(all_test_tasks)} Task rows")
    print(f"  {len(test_image_assets)} ImageAsset rows")
    print(f"  Total: {total} rows")

    confirm = input("\nType YES to proceed: ").strip()
    if confirm != "YES":
        print("Aborted.")
        sys.exit(1)

    # ── Delete (FulfillmentRecords first — reference PODProducts) ─────────────
    for r in all_fr:
        db.delete(r)
    for a in test_image_assets:
        db.delete(a)
    for p in test_pods:
        db.delete(p)
    for t in all_test_tasks:
        db.delete(t)
    db.commit()

    print(f"\nDeleted {len(all_fr)} FulfillmentRecord rows.")
    print(f"Deleted {len(test_image_assets)} ImageAsset rows.")
    print(f"Deleted {len(test_pods)} PODProduct rows.")
    print(f"Deleted {len(all_test_tasks)} Task rows.")

    # ── Verify ─────────────────────────────────────────────────────────────────
    print("\nVerification:")

    rem_fr_stress = (
        db.query(FulfillmentRecord)
        .filter(FulfillmentRecord.etsy_receipt_id.like(f"{STRESS_RECEIPT_PREFIX}%"))
        .count()
    )
    rem_pods = (
        db.query(PODProduct)
        .filter(PODProduct.etsy_listing_id.in_(list(ALL_TEST_LISTING_IDS)))
        .count()
    )
    rem_tasks_stress = (
        db.query(Task).filter(Task.prompt.like(TASK_PROMPT_LIKE_STRESS)).count()
    )
    rem_tasks_concurrent = (
        db.query(Task).filter(Task.prompt.like(TASK_PROMPT_LIKE_CONCURRENT)).count()
    )
    rem_assets = (
        db.query(ImageAsset)
        .filter(ImageAsset.task_id.in_(list(all_test_task_ids)))
        .count()
    ) if all_test_task_ids else 0

    total_fr_now  = db.query(FulfillmentRecord).count()
    total_pods_now = db.query(PODProduct).count()
    total_tasks_now = db.query(Task).count()

    print(f"  STRESS-RECEIPT-* FulfillmentRecords remaining : {rem_fr_stress}")
    print(f"  Test PODProducts remaining                    : {rem_pods}")
    print(f"  'Stress test task %' Tasks remaining          : {rem_tasks_stress}")
    print(f"  'Concurrent write %' Tasks remaining          : {rem_tasks_concurrent}")
    print(f"  Test ImageAssets remaining                    : {rem_assets}")
    print(f"  Total FulfillmentRecords in DB now            : {total_fr_now}")
    print(f"  Total PODProducts in DB now                   : {total_pods_now}")
    print(f"  Total Tasks in DB now                         : {total_tasks_now}")

    all_clean = (
        rem_fr_stress == 0 and rem_pods == 0 and
        rem_tasks_stress == 0 and rem_tasks_concurrent == 0 and rem_assets == 0
    )
    if all_clean:
        print("\nCleanup successful — no test artifacts remain.")
    else:
        print("\nWARNING: some test rows may not have been deleted — check above.")

finally:
    db.close()
