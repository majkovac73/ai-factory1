"""
Step 102 / P0-7 + P0-8 test — receipt worker revenue recording & fulfillment retry.

Uses a throwaway SQLite DB and a temp state file (no real Etsy/Printify calls).

P0-8 (revenue):
  [1] A receipt with a POD transaction AND a digital transaction records ONE
      sale_recorded per transaction, with the right line-total amount, tied to
      the correct task (POD via PODProduct, digital via ImageAsset.listing_id).
  [2] Re-processing the same receipt records NO duplicate sales (idempotent on
      transaction_id).

P0-7 (retry):
  [3] When submit_order raises on the first poll, the receipt is NOT lost: it's
      tracked in failed_receipts, the checkpoint is held back, and the SECOND
      poll retries and succeeds — clearing it.
  [4] A digital-only receipt whose fulfillment can't fail returns ok (no retry).

Usage: python scripts/test_step102_receipt_revenue_retry.py
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmpdir, "receipts_test.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
# import models so their tables are created
from app.models.pod_product import PODProduct
from app.models.image_asset import ImageAsset
from app.models.fulfillment_record import FulfillmentRecord
from app.models.analytics_event import AnalyticsEvent  # noqa: F401
import app.workers.etsy_receipt_worker as erw
from app.services.revenue_service import RevenueService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)
# Point the worker's state file at the temp dir.
erw.STATE_FILE = Path(_tmpdir) / "state.json"

# Seed: a POD product (listing 111 -> task pod-task) and a digital asset
# (listing 222 -> task dig-task).
db = SessionLocal()
db.add(PODProduct(task_id="pod-task", etsy_listing_id="111", variant_ids=[9001]))
db.add(ImageAsset(task_id="dig-task", listing_id="222", variant="delivery",
                  use_case="delivery", agent="ProductImageAgent", local_path="/x.png"))
db.commit()
db.close()


def make_receipt(with_fulfillment_ok=True):
    return {
        "receipt_id": 5001,
        "name": "Jane Doe",
        "first_line": "1 St", "city": "Town", "state": "CA",
        "country_iso": "US", "zip": "90000",
        "buyer_email": "jane@example.com",
        "updated_timestamp": 1000,
        "transactions": [
            {"listing_id": 111, "transaction_id": "t-pod", "quantity": 2,
             "price": {"amount": 2500, "divisor": 100, "currency_code": "USD"}},
            {"listing_id": 222, "transaction_id": "t-dig", "quantity": 1,
             "price": {"amount": 400, "divisor": 100, "currency_code": "USD"}},
        ],
    }


def sales():
    return RevenueService().analytics_service.get_events(event_type="sale_recorded", limit=1000)


# ---- [1] revenue recorded per transaction ----
fulfillment = MagicMock()
worker = erw.EtsyReceiptWorker(fulfillment_service=fulfillment)
ok = worker._process_receipt(make_receipt())
evts = sales()
by_task = {e.entity_id: e for e in evts}
check("1 two sales recorded (POD + digital)", len(evts) == 2)
check("1 POD sale amount = 25.00*2 = 50.00", "pod-task" in by_task and abs((by_task["pod-task"].value or 0) - 50.0) < 1e-6)
check("1 digital sale amount = 4.00", "dig-task" in by_task and abs((by_task["dig-task"].value or 0) - 4.0) < 1e-6)
check("1 receipt ok (fulfillment succeeded)", ok is True)
check("1 buyer email forwarded to submit_order",
      fulfillment.submit_order.call_args.kwargs.get("shipping_address", {}).get("email") == "jane@example.com")

# ---- [2] idempotent: re-process -> still 2 sales ----
worker._process_receipt(make_receipt())
check("2 no duplicate sales on re-process", len(sales()) == 2)

# ---- [3] retry across polls ----
# fresh state
if erw.STATE_FILE.exists():
    erw.STATE_FILE.unlink()

flaky = MagicMock()
flaky.submit_order.side_effect = [Exception("Printify 500"), None]  # fail then succeed
worker2 = erw.EtsyReceiptWorker(fulfillment_service=flaky)

# Only the POD transaction matters for fulfillment; give a POD-only receipt.
pod_only = {
    "receipt_id": 6002, "name": "Al B", "first_line": "2 St", "city": "T",
    "state": "CA", "country_iso": "US", "zip": "90001", "updated_timestamp": 2000,
    "transactions": [
        {"listing_id": 111, "transaction_id": "t-retry", "quantity": 1,
         "price": {"amount": 3000, "divisor": 100, "currency_code": "USD"}},
    ],
}


async def _fake_fetch(min_last_modified=0):
    return [pod_only]

worker2._fetch_receipts = _fake_fetch

worker2._poll_new_receipts()
state1 = worker2._load_state()
check("3 first poll: submit attempted once", flaky.submit_order.call_count == 1)
check("3 first poll: receipt tracked as failed", "6002" in state1.get("failed_receipts", {}))
check("3 first poll: checkpoint held back (< now)", state1.get("last_checked_at", 0) <= 2000)

worker2._poll_new_receipts()
state2 = worker2._load_state()
check("3 second poll: submit retried (called twice total)", flaky.submit_order.call_count == 2)
check("3 second poll: failed cleared after success", "6002" not in state2.get("failed_receipts", {}))

# ---- [4] digital-only receipt: ok, no fulfillment ----
digital_only = {
    "receipt_id": 7003, "name": "Cee D", "updated_timestamp": 3000,
    "transactions": [
        {"listing_id": 222, "transaction_id": "t-dig-only", "quantity": 1,
         "price": {"amount": 500, "divisor": 100, "currency_code": "USD"}},
    ],
}
nofulfil = MagicMock()
worker3 = erw.EtsyReceiptWorker(fulfillment_service=nofulfil)
ok4 = worker3._process_receipt(digital_only)
check("4 digital-only receipt ok", ok4 is True)
check("4 digital-only: no fulfillment submitted", not nofulfil.submit_order.called)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 receipt revenue+retry tests passed.")
