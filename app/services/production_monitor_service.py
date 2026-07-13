"""
ProductionMonitorService (STEP 106 1-9) — watch the single most important
business fact: is the factory actually building products?

The zero-production incident that triggered STEP 106 was only noticed by a human
looking at the shop. Alerting existed for spend caps, dead workers and stale
trends — but not for "AUTONOMY_ENABLED is on and we made 0 products today". This
service provides the daily check (Discord alert once/day) and the dashboard
aggregates (products created in the last 7 days + today's best concept score).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("ai-factory")

_PRODUCT_SOURCES = {"autonomy_worker", "winner_variant", "engagement_variant", "manual_approval"}


class ProductionMonitorService:
    # ── production counts ────────────────────────────────────────────────────
    @staticmethod
    def products_created(hours: int = 24) -> int:
        """Count product tasks created in the last `hours` (autonomy + variants +
        manual approvals)."""
        from app.db.database import SessionLocal
        from app.models.task import Task
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        db = SessionLocal()
        try:
            rows = db.query(Task).filter(Task.created_at >= cutoff).all()
            return sum(1 for t in rows
                       if (t.metadata_ or {}).get("source") in _PRODUCT_SOURCES
                       or (t.type and (t.metadata_ or {}).get("product_name")))
        finally:
            db.close()

    # ── concept scoring stats ────────────────────────────────────────────────
    @staticmethod
    def _weakest_axes(payload: dict, n: int = 2) -> str:
        det = (payload or {}).get("deterministic") or {}
        axes = [(k, v.get("points"), v.get("max"), v.get("why"))
                for k, v in det.items() if isinstance(v, dict) and "points" in v]
        axes.sort(key=lambda a: (a[1] / a[2]) if a[2] else 1)
        return "; ".join(f"{k} {p}/{m} ({why})" for k, p, m, why in axes[:n])

    @classmethod
    def concept_stats_today(cls) -> dict:
        """Today's (UTC) best scored concept + its weakest axes, and the best
        near-miss concept (for 1-10 manual approval)."""
        from app.services.analytics_service import AnalyticsService
        an = AnalyticsService()
        today = datetime.utcnow().date()

        def _today_events(event_type):
            evs = an.get_events(event_type=event_type, limit=1000)
            out = []
            for e in evs:
                ca = getattr(e, "created_at", None)
                if ca and ca.date() == today:
                    out.append(e)
            return out

        scored = _today_events("concept_scored")
        best = max(scored, key=lambda e: e.value or 0, default=None)
        near = _today_events("concept_near_miss")
        best_near = max(near, key=lambda e: e.value or 0, default=None)

        return {
            "scored_count": len(scored),
            "best_total": (best.value if best else None),
            "best_name": (best.entity_id if best else None),
            "best_weakest": (cls._weakest_axes(best.payload) if best else None),
            "best_passed": bool((best.payload or {}).get("passed")) if best else False,
            "near_miss": ((best_near.payload or {}).get("concept") if best_near else None),
            "near_miss_total": (best_near.value if best_near else None),
        }

    # ── the daily zero-production alert ──────────────────────────────────────
    def run_zero_production_check(self) -> dict:
        """If autonomy is on and 0 products were created in the last 24h, alert
        Maj (at most once per UTC day) with the day's best candidate + weakest
        axes. Returns a report dict."""
        from config import settings
        autonomy_on = bool(getattr(settings, "AUTONOMY_ENABLED", False))
        made = self.products_created(24)
        report = {"autonomy_enabled": autonomy_on, "products_24h": made, "alerted": False}
        if not autonomy_on or made > 0:
            return report
        if not self._alert_once_per_day():
            report["alerted"] = "suppressed (already alerted today)"
            return report
        stats = self.concept_stats_today()
        report["stats"] = stats
        best_line = "no concepts were scored today (cycles may be dying before scoring — check logs)"
        if stats["best_total"] is not None:
            best_line = (f"best candidate '{stats['best_name']}' scored {stats['best_total']}: "
                         f"{stats['best_weakest']}")
        try:
            from app.services.alert_service import AlertService
            AlertService().send_alert_sync(
                "Factory produced 0 products today",
                f"AUTONOMY_ENABLED is on but no products were created in the last 24h. "
                f"{stats['scored_count']} concepts scored. {best_line}. "
                "If this persists, the gate may be too tight or cycles are erroring — "
                "review GET /analytics/events?event_type=concept_scored.",
                level="warning",
            )
            report["alerted"] = True
        except Exception as e:
            logger.warning(f"ProductionMonitorService: alert failed: {e}")
        return report

    def _alert_once_per_day(self) -> bool:
        from app.core.paths import get_data_dir
        import time as _t
        try:
            marker = get_data_dir() / "zero_production_alert.json"
            last = 0
            if marker.exists():
                last = json.loads(marker.read_text(encoding="utf-8")).get("at", 0)
            if _t.time() - last < 86400:
                return False
            marker.write_text(json.dumps({"at": _t.time()}), encoding="utf-8")
            return True
        except Exception:
            return True

    # ── dashboard tile ───────────────────────────────────────────────────────
    def dashboard_summary(self) -> dict:
        stats = self.concept_stats_today()
        return {
            "products_last_24h": self.products_created(24),
            "products_last_7d": self.products_created(24 * 7),
            "concepts_scored_today": stats["scored_count"],
            "best_score_today": stats["best_total"],
            "best_concept_today": stats["best_name"],
            "has_near_miss": stats["near_miss"] is not None,
        }
