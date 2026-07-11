from typing import Optional

from app.services.analytics_service import AnalyticsService


class RevenueService:
    """
    Revenue tracking built on top of AnalyticsService (Step 62).

    Real sales are recorded automatically by the EtsyReceiptWorker (P0-8):
    the worker polls paid Etsy receipts (transactions_r scope IS granted
    since step 81) and records one `sale_recorded` event per transaction —
    for BOTH POD and digital listings — tied back to the task/product that
    generated the listing, so performance scoring and best-product
    detection (steps 64-65) correlate revenue with specific AI-generated
    listings. Recording is idempotent on Etsy transaction_id. Manual
    recording via POST /analytics/revenue still exists as a backstop.
    """

    def __init__(self):
        self.analytics_service = AnalyticsService()

    def has_sale_for_transaction(self, transaction_id: str) -> bool:
        """Idempotency guard: True if a sale was already recorded for this
        Etsy transaction_id. D-3: O(1) indexed lookup on the transaction_id
        column instead of scanning 10k rows and reading each JSON payload."""
        if not transaction_id:
            return False
        from app.db.database import SessionLocal
        from app.models.analytics_event import AnalyticsEvent
        db = SessionLocal()
        try:
            return db.query(AnalyticsEvent).filter(
                AnalyticsEvent.event_type == "sale_recorded",
                AnalyticsEvent.transaction_id == str(transaction_id),
            ).first() is not None
        finally:
            db.close()

    def record_sale(
        self,
        task_id: str,
        amount: float,
        currency: str = "USD",
        quantity: int = 1,
        notes: Optional[str] = None,
        transaction_id: Optional[str] = None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("amount must be a positive number")
        if quantity <= 0:
            raise ValueError("quantity must be a positive integer")

        if currency != "USD":
            # D-6: revenue aggregation assumes USD; surface non-USD sales instead
            # of silently summing mixed currencies.
            import logging
            logging.getLogger("ai-factory").warning(
                f"RevenueService: recording non-USD sale ({currency}) for task {task_id}"
            )
        self.analytics_service.record_event(
            event_type="sale_recorded",
            entity_type="task",
            entity_id=task_id,
            value=amount,
            transaction_id=str(transaction_id) if transaction_id else None,  # D-3 column
            payload={
                "currency": currency,
                "quantity": quantity,
                "notes": notes,
                "transaction_id": str(transaction_id) if transaction_id else None,
            },
        )

        return {
            "task_id": task_id,
            "amount": amount,
            "currency": currency,
            "quantity": quantity,
        }

    def get_total_revenue(self, task_id: Optional[str] = None) -> dict:
        events = self.analytics_service.get_events(
            event_type="sale_recorded",
            entity_type="task",
            entity_id=task_id,
            limit=10000,
        )

        total = sum(e.value or 0 for e in events)

        return {
            "total_revenue": total,
            "sale_count": len(events),
            "task_id": task_id,
        }

    def get_revenue_by_task(self) -> dict:
        events = self.analytics_service.get_events(
            event_type="sale_recorded",
            entity_type="task",
            limit=10000,
        )

        breakdown: dict = {}
        for e in events:
            breakdown.setdefault(e.entity_id, 0.0)
            breakdown[e.entity_id] += e.value or 0

        return breakdown