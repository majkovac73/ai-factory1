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

    # ── #11: blocked-task surfacing ──────────────────────────────────────────
    @staticmethod
    def blocked_tasks(hours: int = 24) -> dict:
        """#11: tasks whose post-completion pipeline BLOCKED them (no verified
        product) in the last `hours`, with the top reasons. Blocks are persisted
        as output_data.pipeline_status='BLOCKED_NO_PRODUCT' (not a deletion and not
        task.status, which stays DONE because the task's OWN QA passed — it's the
        downstream listing that was refused). This makes them countable/alertable
        so silent-failure regressions surface."""
        from collections import Counter
        from app.db.database import SessionLocal
        from app.models.task import Task
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        db = SessionLocal()
        try:
            rows = db.query(Task).filter(Task.updated_at >= cutoff).all()
        except Exception:
            # updated_at may be absent on very old rows; fall back to created_at.
            rows = db.query(Task).filter(Task.created_at >= cutoff).all()
        finally:
            db.close()
        blocked, reasons = [], Counter()
        for t in rows:
            od = t.output_data or {}
            if od.get("pipeline_status") == "BLOCKED_NO_PRODUCT":
                reason = str(od.get("pipeline_blocked_reason") or "unknown")
                blocked.append(t.id)
                # bucket by the leading phrase so counts are meaningful
                reasons[reason.split(":")[0].strip()[:60]] += 1
        return {"count": len(blocked), "task_ids": blocked[:50], "top_reasons": reasons.most_common(5)}

    def run_blocked_tasks_check(self) -> dict:
        """#11: daily 'N tasks blocked, top reasons' alert. At most once/UTC day."""
        info = self.blocked_tasks(24)
        report = {"blocked_24h": info["count"], "alerted": False, "top_reasons": info["top_reasons"]}
        if info["count"] == 0:
            return report
        if not self._alert_once_per_day(marker_name="blocked_tasks_alert.json"):
            report["alerted"] = "suppressed (already alerted today)"
            return report
        reasons = "; ".join(f"{r} ({n})" for r, n in info["top_reasons"]) or "see logs"
        try:
            from app.services.alert_service import AlertService
            AlertService().send_alert_sync(
                f"{info['count']} task(s) blocked in the last 24h",
                f"The post-completion pipeline refused to publish {info['count']} product(s) "
                f"(no verified deliverable). Top reasons: {reasons}. "
                "Review GET /dashboard/production or the tasks' output_data.pipeline_blocked_reason.",
                level="warning",
            )
            report["alerted"] = True
        except Exception as e:
            logger.warning(f"ProductionMonitorService: blocked-tasks alert failed: {e}")
        return report

    # ── #3: enforce-mode zero-passer streak guardrail ────────────────────────
    def record_enforce_cycle_outcome(self, produced: bool) -> dict:
        """#3: called once per autonomy cycle. While PRODUCT_SCORE_ENFORCE is on,
        track CONSECUTIVE cycles that produced no passing concept; alert Maj the
        moment the streak reaches PRODUCT_ENFORCE_ZERO_STREAK_ALERT so a gate that
        is too tight for the current CONCEPT_MODEL can't silently halt the factory.
        A produced cycle resets the streak. No-op when enforce is off.

        This complements the daily run_zero_production_check (which is cause-blind
        and fires at most once/day): this fires FAST and specifically attributes
        the halt to the quality gate + names the day's best near-miss."""
        from config import settings
        if not bool(getattr(settings, "PRODUCT_SCORE_ENFORCE", False)):
            self._write_streak(0)
            return {"enforce": False, "streak": 0, "alerted": False}

        threshold = int(getattr(settings, "PRODUCT_ENFORCE_ZERO_STREAK_ALERT", 3))
        if produced:
            self._write_streak(0)
            return {"enforce": True, "streak": 0, "alerted": False}

        streak = self._read_streak() + 1
        self._write_streak(streak)
        report = {"enforce": True, "streak": streak, "alerted": False}
        # Alert exactly when the streak first reaches the threshold (== not >=) so
        # we warn once per breach, not every cycle thereafter.
        if threshold > 0 and streak == threshold:
            stats = self.concept_stats_today()
            near = ""
            if stats.get("near_miss_total") is not None:
                near = (f" Best near-miss today scored {stats['near_miss_total']} "
                        f"(min {int(getattr(settings, 'PRODUCT_MIN_SCORE', 90))}).")
            try:
                from app.services.alert_service import AlertService
                AlertService().send_alert_sync(
                    "Quality gate is blocking ALL production",
                    f"PRODUCT_SCORE_ENFORCE is on and {streak} consecutive cycles produced "
                    f"ZERO passing concepts — the factory is building nothing.{near} "
                    "The gate is likely too tight for the current CONCEPT_MODEL. "
                    "Either set a stronger CONCEPT_MODEL (e.g. anthropic/claude-sonnet-5) "
                    "or lower the floors / set PRODUCT_SCORE_ENFORCE=false until quality recovers. "
                    "See GET /analytics/events?event_type=concept_scored.",
                    level="warning",
                )
                report["alerted"] = True
            except Exception as e:
                logger.warning(f"ProductionMonitorService: enforce-streak alert failed: {e}")
        return report

    def _streak_marker(self):
        from app.core.paths import get_data_dir
        return get_data_dir() / "enforce_zero_passer_streak.json"

    def _read_streak(self) -> int:
        try:
            m = self._streak_marker()
            if m.exists():
                return int(json.loads(m.read_text(encoding="utf-8")).get("streak", 0))
        except Exception:
            pass
        return 0

    def _write_streak(self, streak: int) -> None:
        try:
            self._streak_marker().write_text(json.dumps({"streak": int(streak)}), encoding="utf-8")
        except Exception:
            pass

    def _alert_once_per_day(self, marker_name: str = "zero_production_alert.json") -> bool:
        from app.core.paths import get_data_dir
        import time as _t
        try:
            marker = get_data_dir() / marker_name
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
        blocked = self.blocked_tasks(24)
        return {
            "products_last_24h": self.products_created(24),
            "products_last_7d": self.products_created(24 * 7),
            "concepts_scored_today": stats["scored_count"],
            "best_score_today": stats["best_total"],
            "best_concept_today": stats["best_name"],
            "has_near_miss": stats["near_miss"] is not None,
            "blocked_tasks_24h": blocked["count"],           # #11
            "blocked_top_reasons": blocked["top_reasons"],   # #11
        }
