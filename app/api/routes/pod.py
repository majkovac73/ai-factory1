"""
POD fulfillment routes — step 81-2j, P0-7.

GET  /pod/fulfillments                     — list FulfillmentRecord rows.
POST /pod/fulfillments/resubmit/{id}       — manual recovery: re-process a paid
                                             Etsy receipt (idempotent) when its
                                             automatic fulfillment gave up.

Fulfillment is normally fully automatic (EtsyReceiptWorker); the resubmit
route exists only as a manual backstop for orders that exhausted their
automatic retries.
"""
import logging

from fastapi import APIRouter, HTTPException

from app.db.database import SessionLocal
from app.models.fulfillment_record import FulfillmentRecord

logger = logging.getLogger("ai-factory")

router = APIRouter()


@router.get("/fulfillments")
def list_fulfillments(limit: int = 100):
    """Return recent FulfillmentRecord rows, newest first."""
    db = SessionLocal()
    try:
        records = (
            db.query(FulfillmentRecord)
            .order_by(FulfillmentRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "etsy_receipt_id": r.etsy_receipt_id,
                "task_id": r.task_id,
                "pod_product_id": r.pod_product_id,
                "printify_order_id": r.printify_order_id,
                "status": r.status,
                "tracking_number": r.tracking_number,
                "carrier": r.carrier,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]
    finally:
        db.close()


@router.post("/fulfillments/resubmit/{receipt_id}")
def resubmit_fulfillment(receipt_id: str):
    """Manually re-process a paid Etsy receipt (P0-7). Idempotent: transactions
    already fulfilled/recorded are skipped. Protected by the FACTORY_API_KEY
    middleware (POST)."""
    from app.workers.etsy_receipt_worker import EtsyReceiptWorker
    try:
        result = EtsyReceiptWorker().process_receipt_by_id(receipt_id)
    except Exception as e:
        logger.error(f"resubmit_fulfillment: failed for receipt {receipt_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Could not process receipt: {e}")
    return result
