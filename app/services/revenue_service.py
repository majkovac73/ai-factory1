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

        # DEEP AUDIT V3: revenue is aggregated in the shop's BASE_CURRENCY (EUR).
        # A sale in the base currency is EXPECTED; only a genuinely different
        # currency (mixed-currency shop) is worth surfacing.
        from app.core.currency import base_currency
        if currency != base_currency():
            import logging
            logging.getLogger("ai-factory").warning(
                f"RevenueService: sale currency {currency} != shop base {base_currency()} "
                f"for task {task_id} — P&L may mix currencies"
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
        # 3-4: Etsy Offsite Ads takes 15% when a sale is attributed to it.
        # Attribution isn't in the receipt payload, so estimate it as
        # OFFSITE_ADS_ASSUMED_ATTRIBUTION_PCT of sales × 15% — otherwise net P&L
        # is systematically optimistic (POD pricing already reserves for this).
        offsite_attrib = float(getattr(settings, "OFFSITE_ADS_ASSUMED_ATTRIBUTION_PCT", 0.10))
        offsite_fee = round(sale_amount * offsite_attrib * 0.15, 4)
        fee = round(
            sale_amount * float(getattr(settings, "ETSY_TRANSACTION_FEE_PCT", 0.065))
            + sale_amount * float(getattr(settings, "ETSY_PAYMENT_FEE_PCT", 0.03))
            + float(getattr(settings, "ETSY_PAYMENT_FEE_FLAT", 0.25))
            + offsite_fee,
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
                "offsite_ads_estimate": offsite_fee,
                "basis": f"6.5% transaction + 3% payment + $0.25 flat + "
                         f"{offsite_attrib*100:.0f}%×15% offsite-ads (estimate)",
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

    def get_total_cost(self, task_id: Optional[str] = None) -> dict:
        """#4: total recorded production cost (cost_incurred events) for a task, or
        shop-wide when task_id is None. This is the SPEND side of unit economics —
        image gen + vision-QA + text-LLM dollars attributed per task via
        cost_context."""
        events = self.analytics_service.get_events(
            event_type="cost_incurred", entity_type="task",
            entity_id=task_id, limit=100000,
        )
        by_use: dict = {}
        for e in events:
            uc = (e.payload or {}).get("use_case", "other")
            by_use[uc] = round(by_use.get(uc, 0.0) + (e.value or 0), 6)
        return {
            "total_cost": round(sum(e.value or 0 for e in events), 4),
            "cost_count": len(events),
            "by_use_case": by_use,
            "task_id": task_id,
        }

    def cost_by_task(self) -> dict:
        """#4: {task_id -> total cost_incurred}."""
        events = self.analytics_service.get_events(
            event_type="cost_incurred", entity_type="task", limit=100000,
        )
        out: dict = {}
        for e in events:
            out[e.entity_id] = round(out.get(e.entity_id, 0.0) + (e.value or 0), 6)
        return out

    def pnl_by_listing(self) -> list:
        """#4: per-listing profit & loss — the money view the whole optimization
        loop needs. Joins cost_incurred (spend) + sale_recorded (revenue) −
        fee_estimate (Etsy fees), keyed by task, resolved to its real
        etsy_listing_id. Returns rows sorted by net ascending (worst first) so
        loss-makers surface. A task with cost but no sale shows negative net
        (correct: it's sunk cost until it sells)."""
        costs = self.cost_by_task()
        revenue = self.get_revenue_by_task()
        fees_by_task: dict = {}
        for e in self.analytics_service.get_events(event_type="fee_estimate", entity_type="task", limit=100000):
            fees_by_task[e.entity_id] = round(fees_by_task.get(e.entity_id, 0.0) + (e.value or 0), 6)

        task_ids = set(costs) | set(revenue) | set(fees_by_task)
        if not task_ids:
            return []

        # Resolve task_id -> (listing_id, format, name)
        meta = {}
        try:
            from app.db.database import SessionLocal
            from app.models.task import Task
            from app.services.marketing_refresh_service import MarketingRefreshService
            refresh = MarketingRefreshService()
            db = SessionLocal()
            try:
                for t in db.query(Task).filter(Task.id.in_(list(task_ids))).all():
                    name = (t.output_data or {}).get("title") or t.title or t.id
                    meta[t.id] = {"format": t.type or "unknown", "name": name}
            finally:
                db.close()
            for tid in task_ids:
                meta.setdefault(tid, {"format": "unknown", "name": tid})
                meta[tid]["listing_id"] = refresh.resolve_listing_id(tid)
        except Exception:
            pass

        # DEEP AUDIT V3: costs are USD; revenue + fees are the shop's base currency
        # (EUR). Convert costs to base so `net` is a single currency, not a mix.
        from app.core.currency import usd_to_base, base_currency
        rows = []
        for tid in task_ids:
            m = meta.get(tid, {})
            cost = usd_to_base(costs.get(tid, 0.0))   # USD -> EUR
            rev = round(revenue.get(tid, 0.0), 4)     # already EUR
            fee = round(fees_by_task.get(tid, 0.0), 4)
            rows.append({
                "task_id": tid,
                "listing_id": m.get("listing_id"),
                "format": m.get("format", "unknown"),
                "name": (m.get("name") or tid)[:80],
                "currency": base_currency(),
                "cost": cost,
                "revenue": rev,
                "fees": fee,
                "net": round(rev - fee - cost, 4),
            })
        rows.sort(key=lambda r: r["net"])
        return rows

    def profit_by_format(self) -> dict:
        """3-5: per-format {sales, revenue, fees, net, avg_price} so the learning
        loop can bias by DOLLARS (a $12 planner ~ 4 coloring-page sales), not just
        counts/themes. Maps sale/fee events to each task's product_format."""
        sales = self.analytics_service.get_events(event_type="sale_recorded", entity_type="task", limit=10000)
        fees = self.analytics_service.get_events(event_type="fee_estimate", entity_type="task", limit=10000)
        task_ids = {e.entity_id for e in sales} | {e.entity_id for e in fees}
        if not task_ids:
            return {}
        # resolve task_id -> product_format
        fmt_of = {}
        try:
            from app.db.database import SessionLocal
            from app.models.task import Task
            db = SessionLocal()
            try:
                for t in db.query(Task).filter(Task.id.in_(list(task_ids))).all():
                    fmt_of[t.id] = t.type or "unknown"
            finally:
                db.close()
        except Exception:
            pass

        agg: dict = {}
        for e in sales:
            fmt = fmt_of.get(e.entity_id, "unknown")
            a = agg.setdefault(fmt, {"sales": 0, "revenue": 0.0, "fees": 0.0})
            a["sales"] += 1
            a["revenue"] += e.value or 0
        for e in fees:
            fmt = fmt_of.get(e.entity_id, "unknown")
            a = agg.setdefault(fmt, {"sales": 0, "revenue": 0.0, "fees": 0.0})
            a["fees"] += e.value or 0
        for fmt, a in agg.items():
            a["net"] = round(a["revenue"] - a["fees"], 2)
            a["revenue"] = round(a["revenue"], 2)
            a["fees"] = round(a["fees"], 2)
            a["avg_price"] = round(a["revenue"] / a["sales"], 2) if a["sales"] else 0.0
        return agg