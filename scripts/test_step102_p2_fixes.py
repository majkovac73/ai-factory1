"""
Step 102 / P2-5 + P2-9 test.

P2-5: sync_tracking pushes EVERY shipment's tracking to Etsy (multi-parcel
      orders), not just the first.
P2-9: alert debounce keys on title + message prefix, so two DIFFERENT failures
      sharing a title are BOTH sent; an identical repeat is debounced.

Usage: python scripts/test_step102_p2_fixes.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "p2.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.fulfillment_record import FulfillmentRecord
from app.services.pod_fulfillment_service import PODFulfillmentService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

# ---- P2-5 ----
db = SessionLocal()
db.add(FulfillmentRecord(
    id="fr-1", etsy_receipt_id="r-1", etsy_transaction_id="t-1", task_id="task-1",
    pod_product_id="pod-1", printify_order_id="po-1", status="submitted",
    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
))
db.commit()
db.close()

printify = MagicMock()
printify.get_order_status.return_value = {"shipments": [
    {"number": "TRACK-A", "carrier": "usps"},
    {"number": "TRACK-B", "carrier": "ups"},
]}
svc = PODFulfillmentService(printify_client=printify, selector_agent=MagicMock())

pushes = []
async def _fake_push(receipt_id, number, carrier):
    pushes.append((number, carrier))
svc._push_tracking_to_etsy = _fake_push

ok = svc.sync_tracking("fr-1")
check("P2-5 sync_tracking returned True", ok is True)
check("P2-5 BOTH parcels pushed to Etsy", set(n for n, _ in pushes) == {"TRACK-A", "TRACK-B"})

# ---- P2-9 ----
from app.services import alert_service
alert_service._last_sent.clear()

class _Resp:
    status_code = 204

class _Client:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, json=None, **k): return _Resp()

svc2 = alert_service.AlertService()
svc2._url = "https://example.com/webhook"

with patch.object(alert_service.httpx, "AsyncClient", _Client):
    r1 = asyncio.run(svc2.send_alert("Task blocked", "task AAAA failed: reason one"))
    r2 = asyncio.run(svc2.send_alert("Task blocked", "task BBBB failed: totally different reason two"))
    r3 = asyncio.run(svc2.send_alert("Task blocked", "task AAAA failed: reason one"))  # identical -> debounced

check("P2-9 first alert sent", r1 is True)
check("P2-9 different-message same-title ALSO sent (not collapsed)", r2 is True)
check("P2-9 identical repeat debounced", r3 is False)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 P2 tests passed.")
