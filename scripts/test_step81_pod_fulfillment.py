"""
Step 81 test: Fully automatic Printify POD fulfillment.

Uses test doubles for BOTH PrintifyClient and Etsy receipts — no real API
calls, no real Printify orders. Tests:
  [1] ProductTypeSelectorAgent picks correctly from a fake catalog
  [2] A fake receipt matching a PODProduct auto-creates FulfillmentRecord
      via EtsyReceiptWorker._process_receipt() — NO manual trigger
  [3] Processing the SAME receipt twice does NOT create a second record
      (idempotency)
  [4] A receipt for a listing_id with no matching PODProduct is skipped
      (digital download case)
  [5] sync_tracking() updates FulfillmentRecord to "tracking_synced" from
      a fake Printify order status + calls faked Etsy createReceiptShipment
"""
import os
import sys
import tempfile
import uuid
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch

# Must set DATABASE_URL before any app imports so SessionLocal uses temp DB
_tmp = tempfile.NamedTemporaryFile(suffix=".test.db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 81 — POD FULFILLMENT TEST (test doubles only)")
print("=" * 60)

# Import all models first so they register with Base.metadata, then create tables
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

print("\n[1] ProductTypeSelectorAgent — picks from fake catalog...")

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
print(f"  Selected blueprint_id={result['blueprint_id']} (Ceramic Mug) — correct for mug concept")

# Test fallback when LLM returns garbage
agent_bad = ProductTypeSelectorAgent(provider=FakeLLMProvider(0))
agent_bad.sanitizer = MagicMock()
agent_bad.sanitizer.extract.side_effect = Exception("parse error")

# Patch _generate to return garbage
original_generate = agent_bad._generate
agent_bad._generate = lambda p: "NOT JSON"
result_fallback = agent_bad.select("anything", FAKE_BLUEPRINTS)
assert result_fallback["blueprint_id"] == FAKE_BLUEPRINTS[0]["id"], "Fallback should use first blueprint"
print(f"  Fallback to first blueprint ({result_fallback['blueprint_id']}) on parse error — correct")
agent_bad._generate = original_generate

# ── Seed DB: one PODProduct ──────────────────────────────────────────────────

LISTING_ID = "999000111"
TASK_ID = f"test-{uuid.uuid4().hex[:8]}"
POD_PRODUCT_ID = str(uuid.uuid4())
FAKE_PRINTIFY_PRODUCT_ID = "printify-prod-abc123"

db = SessionLocal()
pod = PODProduct(
    id=POD_PRODUCT_ID,
    task_id=TASK_ID,
    printify_product_id=FAKE_PRINTIFY_PRODUCT_ID,
    blueprint_id=12,
    print_provider_id=3,
    variant_ids=[101, 102, 103],
    etsy_listing_id=LISTING_ID,
    created_at=datetime.utcnow(),
)
db.add(pod)
db.commit()
db.close()

print(f"\n  Seeded PODProduct: listing_id={LISTING_ID}, task_id={TASK_ID}")

# ── Fake receipt fixture ──────────────────────────────────────────────────────

RECEIPT_ID = "RECEIPT-001"
FAKE_RECEIPT = {
    "receipt_id": RECEIPT_ID,
    "name": "Jane Doe",
    "first_line": "123 Maple St",
    "second_line": None,
    "city": "Springfield",
    "state": "IL",
    "zip": "62701",
    "country_iso": "US",
    "transactions": [
        {"listing_id": int(LISTING_ID), "quantity": 1, "transaction_id": 77001},
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
        {"listing_id": 888777666, "quantity": 1},  # no matching PODProduct -> digital
    ],
}

# ── Fake PrintifyClient ───────────────────────────────────────────────────────

class FakePrintifyClient:
    def __init__(self):
        self.orders_created = []
        self.tracking_calls = []

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

    def upload_image(self, image_path): return "fake-img-id"
    def list_blueprints(self): return FAKE_BLUEPRINTS
    def list_print_providers(self, bp): return [{"id": 3, "title": "Monster Digital"}]
    def list_variants(self, bp, pp): return {"variants": [{"id": 101, "is_enabled": True}]}
    def create_product(self, **kw): return {"id": FAKE_PRINTIFY_PRODUCT_ID}

fake_printify = FakePrintifyClient()

# ── [2] Fake receipt -> auto FulfillmentRecord ─────────────────────────────────

print("\n[2] Fake receipt matching PODProduct -> auto FulfillmentRecord (no manual trigger)...")

# Patch _push_tracking_to_etsy to avoid real Etsy call
async def _fake_push_tracking(receipt_id, tracking_number, carrier):
    pass  # no-op

fulfillment_svc = PODFulfillmentService(
    printify_client=fake_printify,
    selector_agent=agent,
)
fulfillment_svc._push_tracking_to_etsy = _fake_push_tracking

worker = EtsyReceiptWorker(
    poll_seconds=9999,
    fulfillment_service=fulfillment_svc,
)

worker._process_receipt(FAKE_RECEIPT)

db = SessionLocal()
records = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == RECEIPT_ID
).all()
db.close()

assert len(records) == 1, f"Expected 1 FulfillmentRecord, got {len(records)}"
record = records[0]
assert record.status == "submitted"
assert record.task_id == TASK_ID
assert len(fake_printify.orders_created) == 1
order = fake_printify.orders_created[0]
assert order["product_id"] == FAKE_PRINTIFY_PRODUCT_ID
assert order["variant_id"] == 101
assert order["address"]["city"] == "Springfield"
assert order["address"]["first_name"] == "Jane"
assert order["address"]["last_name"] == "Doe"
print(f"  FulfillmentRecord created: id={record.id}, printify_order={record.printify_order_id}")
print(f"  Printify order: variant={order['variant_id']}, city={order['address']['city']}")

# ── [3] Same receipt twice -> idempotent ──────────────────────────────────────

print("\n[3] Processing same receipt twice -> no duplicate FulfillmentRecord...")

worker._process_receipt(FAKE_RECEIPT)

db = SessionLocal()
records_after = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == RECEIPT_ID
).all()
db.close()

assert len(records_after) == 1, f"Expected 1 record, got {len(records_after)} after second processing"
assert len(fake_printify.orders_created) == 1, "No second Printify order should have been created"
print("  Still exactly 1 FulfillmentRecord — idempotency holds")

# ── [4] Unmatched listing_id -> skipped ───────────────────────────────────────

print("\n[4] Receipt with no matching PODProduct -> skipped (digital download case)...")

orders_before = len(fake_printify.orders_created)
worker._process_receipt(FAKE_RECEIPT_UNMATCHED)

db = SessionLocal()
unmatched_records = db.query(FulfillmentRecord).filter(
    FulfillmentRecord.etsy_receipt_id == "RECEIPT-DIGITAL"
).all()
db.close()

assert len(unmatched_records) == 0, "Digital-only receipt must not create a FulfillmentRecord"
assert len(fake_printify.orders_created) == orders_before, "No new Printify order for digital receipt"
print("  Correctly skipped — no FulfillmentRecord, no Printify order")

# ── [5] sync_tracking -> "tracking_synced" ────────────────────────────────────

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
assert synced is True, "sync_tracking() should return True when tracking is available"

db = SessionLocal()
updated = db.query(FulfillmentRecord).filter(FulfillmentRecord.id == record.id).first()
db.close()

assert updated.status == "tracking_synced"
assert updated.tracking_number == "9400111899560334671689"
assert updated.carrier == "usps"
assert len(tracking_push_calls) == 1
assert tracking_push_calls[0]["carrier"] == "usps"
assert tracking_push_calls[0]["receipt_id"] == RECEIPT_ID
print(f"  Status: {updated.status}")
print(f"  Tracking: {updated.carrier} {updated.tracking_number}")
print(f"  Etsy createReceiptShipment called once with correct data")

# Calling again should be a no-op (already tracking_synced)
synced_again = fulfillment_svc.sync_tracking(record.id)
assert synced_again is False, "sync_tracking() should return False when already synced"
print("  Second sync_tracking() call -> no-op (already synced)")

# ── Cleanup ───────────────────────────────────────────────────────────────────

engine.dispose()
os.unlink(_tmp.name)

print("\n" + "=" * 60)
print("STEP 81 TEST PASSED")
print("  [1] ProductTypeSelectorAgent: correct selection + graceful fallback")
print("  [2] Fake receipt -> auto FulfillmentRecord, no manual trigger")
print("  [3] Idempotency: same receipt twice = 1 record, 1 Printify order")
print("  [4] Digital receipt correctly skipped")
print("  [5] sync_tracking() -> 'tracking_synced', Etsy push verified")
print("  Zero real Printify or Etsy API calls made.")
print("=" * 60)
