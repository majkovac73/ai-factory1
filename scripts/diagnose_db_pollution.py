"""
Part 1 diagnostic — print every FulfillmentRecord row sorted by created_at.

Run against Railway:
  railway run python scripts/diagnose_db_pollution.py

Output lets us determine whether the step 83 count discrepancy came from
leftover step 81 test rows (listing_id 999000111/999000222) or from real
duplicate FulfillmentRecords created during the race test.
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

db = SessionLocal()
try:
    records = (
        db.query(FulfillmentRecord)
        .order_by(FulfillmentRecord.created_at)
        .all()
    )

    print(f"\n=== FulfillmentRecord rows: {len(records)} total ===\n")
    print(f"{'id[:8]':<10} {'etsy_receipt_id':<30} {'etsy_transaction_id':<25} {'status':<18} {'created_at'}")
    print("-" * 110)
    for r in records:
        print(
            f"{r.id[:8]:<10} {str(r.etsy_receipt_id):<30} "
            f"{str(r.etsy_transaction_id):<25} {r.status:<18} {r.created_at}"
        )

    stress_rows = [r for r in records if str(r.etsy_receipt_id).startswith("STRESS-RECEIPT-")]
    step81_rows = [r for r in records if not str(r.etsy_receipt_id).startswith("STRESS-RECEIPT-")]

    print(f"\n  STRESS-RECEIPT-* rows: {len(stress_rows)}")
    print(f"  Non-stress rows:       {len(step81_rows)}")

    print("\n=== PODProduct rows ===\n")
    pods = db.query(PODProduct).order_by(PODProduct.created_at).all()
    print(f"{'id[:8]':<10} {'task_id[:8]':<12} {'etsy_listing_id':<20} {'created_at'}")
    print("-" * 70)
    for p in pods:
        print(
            f"{p.id[:8]:<10} {str(p.task_id or '')[:8]:<12} "
            f"{str(p.etsy_listing_id or ''):<20} {p.created_at}"
        )

    test_pods = [p for p in pods if str(p.etsy_listing_id or "") in ("999000111", "999000222")]
    print(f"\n  PODProduct rows with test listing_id 999000111/222: {len(test_pods)}")

    print("\n=== Task rows (most recent 10) ===\n")
    tasks = db.query(Task).order_by(Task.created_at.desc()).limit(10).all()
    for t in tasks:
        print(f"  {t.id[:8]}  {t.status:<12}  {str(t.prompt or '')[:60]}  {t.created_at}")

finally:
    db.close()

print("\nDiagnosis complete.\n")
