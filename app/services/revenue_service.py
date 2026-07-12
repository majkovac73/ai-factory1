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

    def record_fee_estimate(
        self,
        task_id: str,
        sale_amount: float,
        currency: str = "USD",
        transaction_id: Optional[str] = None,
    ) -> float:
        """4-1: record an ESTIMATED Etsy fee for a sale so the P&L reflects net,
        not gross. Etsy takes a transaction fee (~6.5%) + payment-processing fee
        (~3% + $0.25 flat) on every sale; ignoring it overstates profit by
        ~9.5%+. This is an estimate (the exact fee also depends on VAT/regulatory
        operating fees per country), recorded as its own `fee_estimate` event so
        it's auditable and never double-counted. Idempotent by construction: the
        caller only reaches here right after a NEW sale_recorded is written."""
        from config import settings
        if sale_amount <= 0:
            return 0.0
        fee = round(
            sale_amount * float(getattr(settings, "ETSY_TRANSACTION_FEE_PCT", 0.065))
            + sale_amount * float(getattr(settings, "ETSY_PAYMENT_FEE_PCT", 0.03))
            + float(getattr(settings, "ETSY_PAYMENT_FEE_FLAT", 0.25)),
            4,
        )
        self.analytics_service.record_event(
            event_type="fee_estimate",
            entity_type="task",
            entity_id=task_id,
            value=fee,
            transaction_id=str(transaction_id) if transaction_id else None,
            payload={
                "currency": currency,
                "sale_amount": sale_amount,
                "transaction_id": str(transaction_id) if transaction_id else None,
                "basis": "6.5% transaction + 3% payment + $0.25 flat (estimate)",
            },
        )
        return fee

    def record_renewal_fee_estimate(self, active_listing_count: int) -> float:
        """105 4-1: Etsy charges $0.20 to auto-renew each active listing every ~4
        months — invisible in the per-sale fee estimate. Called from the monthly
        tick, this records the amortized monthly renewal cost
        (active x $0.20 / 4) as a `fee_estimate` event (basis 'renewal') so the
        P&L's net includes it. 28 listings ~= $17/yr today, growing with the
        catalog. Idempotent by cadence: the caller runs it once per ~30 days."""
        from config import settings
        if not active_listing_count or active_listing_count <= 0:
            return 0.0
        per = float(getattr(settings, "ETSY_LISTING_RENEWAL_FEE", 0.20))
        months = float(getattr(settings, "ETSY_LISTING_RENEWAL_MONTHS", 4))
        fee = round(active_listing_count * per / months, 4)
        self.analytics_service.record_event(
            event_type="fee_estimate",
            entity_type="shop",
            entity_id="renewals",
            value=fee,
            payload={
                "basis": "renewal",
                "active_listings": int(active_listing_count),
                "per_listing": per,
                "renewal_months": months,
                "detail": f"{active_listing_count} active x ${per}/{months:.0f}mo amortized monthly",
            },
        )
        return fee

    def get_total_fees(self, task_id: Optional[str] = None) -> dict:
        """4-1: total estimated Etsy fees. Per-sale fees are entity_type='task';
        renewal fees (105 4-1) are entity_type='shop'. For the shop-wide P&L
        (task_id=None) sum ALL fee_estimate events regardless of entity_type;
        for a single task, only that task's per-sale fees."""
        if task_id is not None:
            events = self.analytics_service.get_events(
                event_type="fee_estimate", entity_type="task",
                entity_id=task_id, limit=10000,
            )
        else:
            events = self.analytics_service.get_events(
                event_type="fee_estimate", limit=10000,
            )
        return {
            "total_fees": round(sum(e.value or 0 for e in events), 4),
            "fee_count": len(events),
            "task_id": task_id,
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