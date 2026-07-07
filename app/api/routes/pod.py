"""
POD fulfillment read-only routes — step 81-2j.

GET /pod/fulfillments — list FulfillmentRecord rows for visibility/debugging.

This automation is fully automatic (EtsyReceiptWorker handles everything);
there are no manual-trigger routes here by design.
"""
from fastapi import APIRouter
from typing import List

from app.db.database import SessionLocal
from app.models.fulfillment_record import FulfillmentRecord

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
