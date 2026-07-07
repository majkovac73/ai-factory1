from typing import Optional

from app.services.analytics_service import AnalyticsService


class RevenueService:
    """
    Revenue tracking built on top of AnalyticsService (Step 62).

    This app creates Etsy listings as DRAFTS only and does not integrate
    with Etsy's transactions/receipts API (no transactions_r scope
    requested, consistent with the README's "does not interact with...
    orders" policy). Real sales data therefore isn't pulled
    automatically — revenue is recorded manually by the shop owner
    (e.g. after checking Etsy's own Shop Manager sales dashboard) and
    tied back to the task/product that generated the listing, so later
    steps (64-65: performance scoring, best-product detection) can
    correlate revenue with specific AI-generated listings.
    """

    def __init__(self):
        self.analytics_service = AnalyticsService()

    def record_sale(
        self,
        task_id: str,
        amount: float,
        currency: str = "USD",
        quantity: int = 1,
        notes: Optional[str] = None,
    ) -> dict:
        if amount <= 0:
            raise ValueError("amount must be a positive number")
        if quantity <= 0:
            raise ValueError("quantity must be a positive integer")

        self.analytics_service.record_event(
            event_type="sale_recorded",
            entity_type="task",
            entity_id=task_id,
            value=amount,
            payload={
                "currency": currency,
                "quantity": quantity,
                "notes": notes,
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