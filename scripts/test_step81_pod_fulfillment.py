"""
Step 81 test: Fully automatic Printify POD fulfillment.

Uses test doubles for BOTH PrintifyClient and Etsy receipts — no real API
calls, no real Printify orders. Tests:
  [1] ProductTypeSelectorAgent picks correctly from a fake catalog
  [2] A fake receipt matching a PODProduct auto-creates FulfillmentRecord
      via EtsyReceiptWorker._process_receipt() — NO manual trigger
  [3] Processing the SAME receipt+transaction twice = still only 1 record
      (per-transaction idempotency)
  [4] A receipt for a listing_id with no matching PODProduct is skipped
      (digital download case)
  [5] sync_tracking() updates FulfillmentRecord to "tracking_synced" from
      a fake Printify order status + calls faked Etsy createReceiptShipment
  [6] NEW: One receipt with TWO different POD transactions -> TWO separate
      FulfillmentRecords and TWO separate Printify orders (multi-item fix)
  [7] NEW: Replaying the multi-item receipt does not create duplicates
"""
import os
import sys
import tempfile
import uuid
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

# Must set DATABASE_URL before any app imports so SessionLocal uses temp DB
_tmp = tempfile.NamedTemporaryFile(suffix=".test.db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 81 -- POD FULFILLMENT TEST (test doubles only)")
print("=" * 60)

# Import all models so they register with Base.metadata before create_all
from app.db.database import Base, engine, SessionLocal
import app.models.agent_execution  # noqa
import app.models.log               # noqa
import app.models.memory            # noqa
import app.models.task              # noqa
import app.models.task_step         # noqa
import app.models.etsy_token        # noqa
import app.models.marketing_post    # noqa
import app.models.pinterest_token   # noqa
import app.models.analytics_event   # noqa
import app.models.image_asset       # noqa
from app.models.pod_product import PODProduct
from app.models.fulfillment_record import FulfillmentRecord
Base.metadata.create_all(bind=engine)

from app.agents.product_type_selector_agent import ProductTypeSelectorAgent
from app.services.pod_fulfillment_service import PODFulfillmentService
from app.workers.etsy_receipt_worker import EtsyReceiptWorker

# ── Fake catalog ──────────────────────────────────────────────────────────────

FAKE_BLUEPRINTS = [
    {"id": 5, "title": "Unisex Heavy Cotton Tee"},
    {"id": 6, "title": "Unisex Fleece Hoodie"},
    {"id": 12, "title": "Ceramic Mug 11oz"},
    {"id": 25, "title": "Canvas Wall Art"},
    {"id": 77, "title": "Spiral Notebook"},
]

# ── [1] ProductTypeSelectorAgent picks from fake catalog ──────────────────────

print("\n[1] ProductTypeSelectorAgent -- picks from fake catalog...")

class FakeLLMProvider:
    def __init__(self, forced_blueprint_id: int):
        self._bid = forced_blueprint_id
        self.last_usage = None

    async def generate(self, model, prompt):
        return f'{{"blueprint_id": {self._bid}}}'

agent = ProductTypeSelectorAgent(provider=FakeLLMProvider(12))
result = agent.select(
    concept="A cozy mug design with botanical illustrations",
    blueprints=FAKE_BLUEPRINTS,
)
assert result["blueprint_id"] == 12, f"Expected 12, got {result['blueprint_id']}"
print(f"  Selected blueprint_id={result['blueprint_id']} (Ceramic Mug) -- correct for mug concept")

# Fallback when LLM returns garbage
agent_bad = ProductTypeSelectorAgent(provider=FakeLLMProvider(0))
agent_bad.sanitizer = MagicMock()
agent_bad.sanitizer.extract.side_effect = Exception("parse error")
agent_bad._generate = lambda p: "NOT JSON"
result_fallback = agent_bad.select("anything", FAKE_BLUEPRINTS)
assert result_fallback["blueprint_id"] == FAKE_BLUEPRINTS[0]["id"], "Fallback should use first blueprint"
print(f"  Fallback to first blueprint ({result_fallback['blueprint_id']}) on parse error -- correct")

# ── Seed DB: two PODProducts for multi-item tests ────────────────────────────

LISTING_ID_A = "999000111"
LISTING_ID_B = "999000222"
TASK_ID_A = f"test-{uuid.uuid4().hex[:8]}"
TASK_ID_B = f"test-{uuid.uuid4().hex[:8]}"
POD_ID_A = str(uuid.uuid4())
POD_ID_B = str(uuid.uuid4())
FAKE_PRODUCT_ID_A = "printify-prod-aaa"
FAKE_PRODUCT_ID_B = "printify-prod-bbb"

db = SessionLocal()
db.add(PODProduct(
    id=POD_ID_A, task_id=TASK_ID_A,
    printify_product_id=FAKE_PRODUCT_ID_A,
    blueprint_id=12, print_provider_id=3,
    variant_ids=[101], etsy_listing_id=LISTING_ID_A,
    created_at=datetime.utcnow(),
))
db.add(PODProduct(
    id=POD_ID_B, task_id=TASK_ID_B,
    printify_product_id=FAKE_PRODUCT_ID_B,
    blueprint_id=25, print_provider_id=3,
    variant_ids=[201], etsy_listing_id=LISTING_ID_B,
    created_at=datetime.utcnow(),
))
db.commit()
db.close()

print(f"\n  Seeded PODProduct A: listing={LISTING_ID_A}")
print(f"  Seeded PODProduct B: listing={LISTING_ID_B}")

# ── Fake receipts ─────────────────────────────────────────────────────────────

RECEIPT_ID_SINGLE = "RECEIPT-SINGLE"
TXN_ID_A = "77001"

FAKE_RECEIPT_SINGLE = {
    "receipt_id": RECEIPT_ID_SINGLE,
    "name": "Jane Doe",
    "first_line": "123 Maple St",
    "second_line": None,
    "city": "Springfield",
    "state": "IL",
    "zip": "62701",
    "country_iso": "US",
    "transactions": [
        {"listing_id": int(LISTING_ID_A), "quantity": 1, "transaction_id": int(TXN_ID_A)},
    ],
}

FAKE_RECEIPT_UNMATCHED = {
    "receipt_id": "RECEIPT-DIGITAL",
    "name": "Bob Builder",
    "first_line": "456 Oak Ave",
    "second_line": None,
    "city": "Chicago",
    "state": "IL",
    "zip": "60601",
    "country_iso": "US",
    "transactions": [
        {"listing_id": 888777666, "quantity": 1, "transaction_id": 99999},
    ],
}

# Multi-item receipt: two POD listings in one Etsy order
RECEIPT_ID_MULTI = "RECEIPT-MULTI"
TXN_ID_B = "88002"

FAKE_RECEIPT_MULTI = {
    "receipt_id": RECEIPT_ID_MULTI,
    "name": "Alice Smith",
    "first_line": "789 Birch Rd",
    "second_line": None,
    "city": "Portland",
    "state": "OR",
    "zip": "97201",
    "country_iso": "US",
    "transactions": [
        {"listing_id": int(LISTING_ID_A), "quantity": 1, "transaction_id": 88001},
        {"listing_id": int(LISTING_ID_B), "quantity": 2, "transaction_id": int(TXN_ID_B)},
    ],
}

# ── Fake PrintifyClient ───────────────────────────────────────────────────────

class FakePrintifyClient:
    def __init__(self):
        self.orders_created = []

    def create_order(self, product_id, variant_id, quantity, shipping_address):
        order_id = f"fake-order-{uuid.uuid4().hex[:6]}"
        self.orders_created.append({
            "order_id": order_id,
            "product_id": product_id,
            "variant_id": variant_id,
            "quantity": quantity,
            "address": shipping_address,
        })
        return order_id

    def get_order_status(self, order_id):
        return {
            "status": "fulfilled",
            "shipments": [
                {"carrier": "usps", "number": "9400111899560334671689", "url": "", "delivered_at": None}
            ],
        }

    def upload_image(self, p): return "fake-img-id"
    def list_blueprints(self): return FAKE_BLUEPRINTS
    def list_print_providers(self, bp): return [{"id": 3, "title": "Monster Digital"}]
    def list_variants(self, bp, pp): return {"variants": [{"id": 101, "is_enabled": True}]}
    def create_product(self, **kw): return {"id": FAKE_PRODUCT_ID_A}

fake_printify = FakePrintifyClient()

async def _noop_push(receipt_id, tracking_number, carrier):
    pass

fulfillment_svc = PODFulfillmentService(
    printify_client=fake_printify,
    selector_agent=agent,
)
fulfillment_svc._push_tracking_to_etsy = _noop_push

worker = EtsyReceiptWorker(poll_seconds=9999, fulfillment_service=fulfillment_svc)

# ── [2] Single receipt -> FulfillmentRecord ───────────────────────────────────

print("\n[2] Single-item receipt -> auto FulfillmentRecord (no manual trigger)...")

worker._process_receipt(FAKE_RECEIPT_SINGLE)

db = SessionLocal()
records = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == RECEIPT_ID_SINGLE
).all()
db.close()

assert len(records) == 1, f"Expected 1 FulfillmentRecord, got {len(records)}"
record = records[0]
assert record.status == "submitted"
assert record.task_id == TASK_ID_A
assert record.etsy_transaction_id == TXN_ID_A
assert len(fake_printify.orders_created) == 1
order = fake_printify.orders_created[0]
assert order["product_id"] == FAKE_PRODUCT_ID_A
assert order["variant_id"] == 101
assert order["address"]["city"] == "Springfield"
assert order["address"]["first_name"] == "Jane"
assert order["address"]["last_name"] == "Doe"
print(f"  FulfillmentRecord: id={record.id}, txn_id={record.etsy_transaction_id}")
print(f"  Printify order: product={order['product_id']}, variant={order['variant_id']}")

# ── [3] Same receipt+transaction twice -> idempotent ─────────────────────────

print("\n[3] Same receipt processed twice -> no duplicate (per-transaction idempotency)...")

worker._process_receipt(FAKE_RECEIPT_SINGLE)

db = SessionLocal()
records_after = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == RECEIPT_ID_SINGLE
).all()
db.close()

assert len(records_after) == 1, f"Expected 1, got {len(records_after)} after second processing"
assert len(fake_printify.orders_created) == 1, "No second Printify order should be created"
print("  Still exactly 1 FulfillmentRecord -- per-transaction idempotency holds")

# ── [4] Unmatched listing_id -> skipped ──────────────────────────────────────

print("\n[4] Receipt with no matching PODProduct -> skipped (digital download)...")

orders_before = len(fake_printify.orders_created)
worker._process_receipt(FAKE_RECEIPT_UNMATCHED)

db = SessionLocal()
unmatched = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == "RECEIPT-DIGITAL"
).all()
db.close()

assert len(unmatched) == 0, "Digital receipt must not create a FulfillmentRecord"
assert len(fake_printify.orders_created) == orders_before
print("  Correctly skipped -- no FulfillmentRecord, no Printify order")

# ── [5] sync_tracking -> "tracking_synced" ───────────────────────────────────

print("\n[5] sync_tracking() -> FulfillmentRecord updated to 'tracking_synced'...")

tracking_push_calls = []

async def _capture_push(receipt_id, tracking_number, carrier):
    tracking_push_calls.append({
        "receipt_id": receipt_id,
        "tracking_number": tracking_number,
        "carrier": carrier,
    })

fulfillment_svc._push_tracking_to_etsy = _capture_push

synced = fulfillment_svc.sync_tracking(record.id)
assert synced is True

db = SessionLocal()
updated = db.query(FulfillmentRecord).filter(FulfillmentRecord.id == record.id).first()
db.close()

assert updated.status == "tracking_synced"
assert updated.tracking_number == "9400111899560334671689"
assert updated.carrier == "usps"
assert len(tracking_push_calls) == 1
assert tracking_push_calls[0]["carrier"] == "usps"
assert tracking_push_calls[0]["receipt_id"] == RECEIPT_ID_SINGLE
print(f"  Status: {updated.status}, tracking: {updated.carrier} {updated.tracking_number}")

synced_again = fulfillment_svc.sync_tracking(record.id)
assert synced_again is False, "Second call must be no-op"
print("  Second sync_tracking() call -> no-op (already synced)")

fulfillment_svc._push_tracking_to_etsy = _noop_push

# ── [6] NEW: Multi-item receipt -> TWO FulfillmentRecords ────────────────────

print("\n[6] NEW: Multi-item receipt (2 POD transactions) -> 2 FulfillmentRecords...")

orders_before = len(fake_printify.orders_created)
worker._process_receipt(FAKE_RECEIPT_MULTI)

db = SessionLocal()
multi_records = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == RECEIPT_ID_MULTI
).all()
db.close()

assert len(multi_records) == 2, (
    f"Expected 2 FulfillmentRecords for multi-item receipt, got {len(multi_records)}. "
    f"This was the correctness bug: the old per-receipt uniqueness would have "
    f"created 1 record and silently dropped the second fulfillment."
)
new_orders = len(fake_printify.orders_created) - orders_before
assert new_orders == 2, f"Expected 2 new Printify orders, got {new_orders}"

txn_ids_in_db = {r.etsy_transaction_id for r in multi_records}
assert "88001" in txn_ids_in_db, "Transaction 88001 must have a FulfillmentRecord"
assert TXN_ID_B in txn_ids_in_db, f"Transaction {TXN_ID_B} must have a FulfillmentRecord"

# Verify the second transaction's quantity was passed through
order_for_b = next(
    o for o in fake_printify.orders_created
    if o["product_id"] == FAKE_PRODUCT_ID_B
)
assert order_for_b["quantity"] == 2, f"Expected qty=2 for listing B, got {order_for_b['quantity']}"

print(f"  Two FulfillmentRecords created: txn_ids={txn_ids_in_db}")
print(f"  Two Printify orders submitted ({new_orders} new orders)")
print(f"  Listing B order quantity correctly passed through: qty={order_for_b['quantity']}")

# ── [7] NEW: Replaying multi-item receipt -> idempotent ──────────────────────

print("\n[7] NEW: Replaying multi-item receipt -> no duplicates...")

orders_before = len(fake_printify.orders_created)
worker._process_receipt(FAKE_RECEIPT_MULTI)

db = SessionLocal()
multi_records_after = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == RECEIPT_ID_MULTI
).all()
db.close()

assert len(multi_records_after) == 2, f"Expected 2 records, got {len(multi_records_after)}"
assert len(fake_printify.orders_created) - orders_before == 0, "No new orders on replay"
print("  Still exactly 2 FulfillmentRecords -- multi-item idempotency holds")

# ── Cleanup ───────────────────────────────────────────────────────────────────

engine.dispose()
os.unlink(_tmp.name)

print("\n" + "=" * 60)
print("STEP 81 TEST PASSED (7/7 assertions)")
print("  [1] ProductTypeSelectorAgent: correct selection + graceful fallback")
print("  [2] Single-item receipt -> FulfillmentRecord, no manual trigger")
print("  [3] Per-transaction idempotency: same receipt+txn twice = 1 record")
print("  [4] Digital receipt correctly skipped")
print("  [5] sync_tracking() -> 'tracking_synced', Etsy push verified")
print("  [6] NEW: 2-item receipt -> 2 FulfillmentRecords, 2 Printify orders")
print("  [7] NEW: Multi-item replay -> idempotent (no duplicates)")
print("  Zero real Printify or Etsy API calls made.")
print("=" * 60)
