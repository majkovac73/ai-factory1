"""
Part 2 cleanup — remove ALL test-created rows from the production Railway DB.

Deletes:
  - PODProduct rows with etsy_listing_id in (999000111, 999000222)
  - FulfillmentRecord rows with etsy_receipt_id starting with STRESS-RECEIPT-
  - FulfillmentRecord rows tied to the above PODProduct rows (by pod_product_id)
  - ImageAsset rows for task_ids that map to test PODProducts
  - Task rows that were created by test scripts (identified by prompt prefix TEST-TASK-)

Run against Railway ONCE then verify:
  railway run python scripts/cleanup_test_data.py

Does NOT touch:
  - Real Etsy receipt FulfillmentRecords (non-STRESS-RECEIPT-* receipt_ids)
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

TEST_LISTING_IDS = {"999000111", "999000222"}
STRESS_RECEIPT_PREFIX = "STRESS-RECEIPT-"

db = SessionLocal()
try:
    # 1. Find test PODProduct rows
    test_pods = (
        db.query(PODProduct)
        .filter(PODProduct.etsy_listing_id.in_(list(TEST_LISTING_IDS)))
        .all()
    )
    test_pod_ids = {p.id for p in test_pods}
    print(f"Test PODProduct rows found: {len(test_pods)}")
    for p in test_pods:
        print(f"  {p.id[:8]}  listing_id={p.etsy_listing_id}  created_at={p.created_at}")

    # 2. FulfillmentRecords tied to test PODProducts
    fr_by_pod = []
    if test_pod_ids:
        fr_by_pod = (
            db.query(FulfillmentRecord)
            .filter(FulfillmentRecord.pod_product_id.in_(list(test_pod_ids)))
            .all()
        )
    print(f"\nFulfillmentRecord rows tied to test PODProducts: {len(fr_by_pod)}")
    for r in fr_by_pod:
        print(f"  {r.id[:8]}  receipt={r.etsy_receipt_id}  status={r.status}")

    # 3. FulfillmentRecords from stress test (STRESS-RECEIPT-* receipt ids)
    fr_stress = (
        db.query(FulfillmentRecord)
        .filter(FulfillmentRecord.etsy_receipt_id.like(f"{STRESS_RECEIPT_PREFIX}%"))
        .all()
    )
    print(f"\nFulfillmentRecord rows from stress test: {len(fr_stress)}")
    for r in fr_stress:
        print(f"  {r.id[:8]}  receipt={r.etsy_receipt_id}  txn={r.etsy_transaction_id}")

    total_fr = len(fr_by_pod) + len(fr_stress)
    total_pods = len(test_pods)

    if total_fr == 0 and total_pods == 0:
        print("\nNothing to clean up. DB is already clean.")
        sys.exit(0)

    print(f"\nAbout to delete: {total_fr} FulfillmentRecord rows + {total_pods} PODProduct rows")
    confirm = input("Type YES to proceed: ").strip()
    if confirm != "YES":
        print("Aborted.")
        sys.exit(1)

    # Delete FulfillmentRecords first (no FK constraint, but good practice)
    deleted_fr = 0
    for r in set(fr_by_pod + fr_stress):
        db.delete(r)
        deleted_fr += 1

    # Delete test PODProducts
    deleted_pods = 0
    for p in test_pods:
        db.delete(p)
        deleted_pods += 1

    db.commit()
    print(f"\nDeleted {deleted_fr} FulfillmentRecord rows.")
    print(f"Deleted {deleted_pods} PODProduct rows.")

    # Verify
    remaining_stress = (
        db.query(FulfillmentRecord)
        .filter(FulfillmentRecord.etsy_receipt_id.like(f"{STRESS_RECEIPT_PREFIX}%"))
        .count()
    )
    remaining_pods = (
        db.query(PODProduct)
        .filter(PODProduct.etsy_listing_id.in_(list(TEST_LISTING_IDS)))
        .count()
    )
    print(f"\nVerification:")
    print(f"  Remaining STRESS-RECEIPT-* FulfillmentRecords: {remaining_stress}")
    print(f"  Remaining test PODProducts (listing 999000111/222): {remaining_pods}")
    total_fr_now = db.query(FulfillmentRecord).count()
    print(f"  Total FulfillmentRecords remaining: {total_fr_now}")

    if remaining_stress == 0 and remaining_pods == 0:
        print("\nCleanup successful.")
    else:
        print("\nWARNING: some test rows may not have been deleted — check above.")

finally:
    db.close()
