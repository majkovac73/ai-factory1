from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from app.schemas.enums import TaskStatus
from app.services.task_service import TaskService
from app.services.task_queue import TaskQueue
from app.services.log_service import LogService

router = APIRouter()
task_service = TaskService()
task_queue = TaskQueue()
log_service = LogService()


# ── Live-activity plumbing ───────────────────────────────────────────────────
# Which log sources belong to each room/agent group. A log line is attributed to
# a group when its `source` is one of these names OR it's an "ai-factory" logger
# line whose message starts with "<Name>: ..." (WARNING/ERROR go through the
# python logger, which stamps source='ai-factory' but keeps the class prefix).
AGENT_GROUPS = {
    "research":     {"label": "Research",     "names": ["AutonomyWorker", "TrendResearchAgent", "ResearchAgent", "IntelligenceAgent", "ProductViabilityCriticAgent", "ProductScoreService", "ProductTypeSelectorAgent"]},
    "planning":     {"label": "Planning",     "names": ["TaskWorker", "TaskProcessor", "TaskService", "TaskQueue", "PlannerAgent"]},
    "content":      {"label": "Content",      "names": ["ExecutorAgent", "ListingGeneratorAgent", "SEOGeneratorAgent", "SEOAgent", "PipelineOrchestrator"]},
    "image_studio": {"label": "Image Studio", "names": ["ProductImageAgent", "SocialImageAgent", "PODDesignAgent", "ImageValidationService", "PDFGenerationService", "MockupService", "OpenRouterImageProvider"]},
    "qa":           {"label": "QA",           "names": ["QAAgent", "QAValidator", "QARepairAgent", "ContentQAService"]},
    "storefront":   {"label": "Storefront",   "names": ["EtsyClient", "EtsyImageService", "EtsyShippingService", "EtsyOAuth"]},
    "marketing":    {"label": "Marketing",    "names": ["MarketingService", "PinterestChannel", "TumblrChannel", "MarketingRefreshWorker", "PinterestClient"]},
    "fulfillment":  {"label": "Fulfillment",  "names": ["EtsyReceiptWorker", "PODFulfillmentService", "PrintifyClient"]},
    "ledger":       {"label": "Ledger",       "names": ["AnalyticsService", "RevenueService", "PerformanceService", "BestProductsService"]},
}

# active (non-terminal) task states — what is "in flight" right now
_ACTIVE_STATES = ["NEW", "PLANNED", "RUNNING", "QA"]


def _agent_of(source: str, message: str) -> str:
    """Best display name for a log line: the source unless it's the generic
    'ai-factory' logger, in which case pull the 'ClassName:' prefix from the msg."""
    if source and source != "ai-factory":
        return source
    msg = message or ""
    if ":" in msg:
        head = msg.split(":", 1)[0].strip()
        if 0 < len(head) <= 40 and " " not in head:
            return head
    return source or "system"


def _fmt_log(row) -> dict:
    msg = (row.message or "")
    agent = _agent_of(row.source, msg)
    # the agent has its own column — drop a redundant leading "Agent: " prefix
    if msg.startswith(f"{agent}:"):
        msg = msg[len(agent) + 1:].lstrip()
    return {
        "level": row.level,
        "source": row.source,
        "agent": agent,
        "message": msg[:200],
        "at": row.created_at.isoformat() if row.created_at else None,
    }


# Routine background chatter that isn't "the factory doing something" — hidden
# from the global feed so real production activity isn't buried (still visible in
# the owning agent's own drill-down).
_NOISE_MESSAGES = ["Etsy access token refreshed"]


def _recent_logs(limit: int = 60, names: list = None, exclude_noise: bool = False):
    """Recent log rows (all levels), newest first. If `names` is given, keep only
    lines belonging to that agent group (direct source match OR 'ai-factory' line
    prefixed 'Name: ...'). If `exclude_noise`, drop routine heartbeat chatter."""
    from app.db.database import SessionLocal
    from app.models.log import Log
    from sqlalchemy import or_
    db = SessionLocal()
    try:
        q = db.query(Log)
        if names:
            conds = [Log.source.in_(names)]
            for n in names:
                conds.append(Log.message.like(f"{n}:%"))
            q = q.filter(or_(*conds))
        if exclude_noise:
            q = q.filter(Log.message.notin_(_NOISE_MESSAGES))
        rows = q.order_by(Log.created_at.desc()).limit(int(limit)).all()
        return [_fmt_log(r) for r in rows]
    finally:
        db.close()


def _running_tasks():
    """Tasks currently in flight, with a human product name + elapsed seconds."""
    out = []
    now = datetime.utcnow()
    for t in task_service.list_tasks():
        if t.status not in _ACTIVE_STATES:
            continue
        od = t.output_data or {}
        md = t.metadata_ or {}
        name = od.get("title") or md.get("product_name") or md.get("concept") or t.title or "(unnamed)"
        elapsed = None
        if t.created_at:
            elapsed = round((now - t.created_at).total_seconds())
        out.append({
            "id": t.id,
            "short_id": (t.id or "")[:8],
            "type": t.type,
            "status": t.status,
            "product_name": str(name)[:80],
            "elapsed_s": elapsed,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    # longest-running first (most likely the one actively worked)
    out.sort(key=lambda r: r["elapsed_s"] or 0, reverse=True)
    return out


@router.get("/pnl")
def pnl():
    """D-6/4-1: the one number that matters — lifetime revenue vs spend, with an
    HONEST net. Gross is what buyers paid; Etsy then takes ~9.5% + $0.25 per
    order (transaction + payment fees), so net_revenue = gross - estimated fees.
    Profit is net_revenue minus our production spend (image/vision/text + Etsy
    listing fees). Reporting only gross would overstate profit by ~10% of sales."""
    from app.services.autonomy_service import AutonomyService
    from app.services.revenue_service import RevenueService
    from app.core.currency import usd_to_base, base_currency
    spend_usd = AutonomyService().lifetime_spend()
    spend_base = usd_to_base(spend_usd)   # DEEP AUDIT V3: costs are USD, revenue EUR
    rs = RevenueService()
    rev = rs.get_total_revenue()
    fees = rs.get_total_fees()
    gross = round(rev.get("total_revenue", 0.0) or 0.0, 2)   # EUR
    etsy_fees = round(fees.get("total_fees", 0.0) or 0.0, 2)  # EUR
    net_revenue = round(gross - etsy_fees, 2)
    cur = base_currency()
    return {
        "currency": cur,                      # everything below is in this currency (EUR)
        "gross_revenue": gross,
        "etsy_fees": etsy_fees,
        "net_revenue": net_revenue,
        "spend": round(spend_base, 2),        # USD spend converted to EUR
        "spend_usd_raw": round(spend_usd, 2), # original USD, for reference
        "profit": round(net_revenue - spend_base, 2),
        "sales": rev.get("sale_count", 0),
        # legacy keys kept for any existing dashboard consumers (now EUR-normalized)
        "revenue_usd": gross,
        "gross_revenue_usd": gross,
        "etsy_fees_usd": etsy_fees,
        "net_revenue_usd": net_revenue,
        "spend_usd": round(spend_base, 2),
        "profit_usd": round(net_revenue - spend_base, 2),
    }


@router.get("/pnl-by-listing")
def pnl_by_listing(limit: int = 100):
    """#4: per-listing profit & loss (worst net first) so loss-makers surface —
    joins per-task production cost (cost_incurred) + revenue − Etsy fees, resolved
    to each real etsy_listing_id. Enables doubling down on winners and cutting
    uniform losers instead of flying blind on unit economics."""
    from app.services.revenue_service import RevenueService
    rows = RevenueService().pnl_by_listing()
    return {
        "count": len(rows),
        "rows": rows[: max(1, int(limit))],
        "totals": {
            "cost": round(sum(r["cost"] for r in rows), 2),
            "revenue": round(sum(r["revenue"] for r in rows), 2),
            "fees": round(sum(r["fees"] for r in rows), 2),
            "net": round(sum(r["net"] for r in rows), 2),
        },
    }


@router.get("/production")
def production_summary():
    """1-9: the single most important business fact — is the factory building
    products? Products created in the last 24h / 7d + today's best concept score."""
    from app.services.production_monitor_service import ProductionMonitorService
    return ProductionMonitorService().dashboard_summary()


@router.get("/overview")
def dashboard_overview():
    all_tasks = task_service.list_tasks()

    status_counts = {status.value: 0 for status in TaskStatus}
    for task in all_tasks:
        if task.status in status_counts:
            status_counts[task.status] += 1

    recent_errors = log_service.list_logs(level="ERROR", limit=10)

    return {
        "total_tasks": len(all_tasks),
        "status_counts": status_counts,
        "queue_size": task_queue.size(),
        "recent_errors": [
            {
                "source": log.source,
                "message": log.message,
                "created_at": log.created_at,
            }
            for log in recent_errors
        ],
    }

@router.get("/metrics")
def dashboard_metrics():
    all_tasks = task_service.list_tasks()

    done_tasks = [t for t in all_tasks if t.status == TaskStatus.DONE.value]
    failed_tasks = [t for t in all_tasks if t.status == TaskStatus.FAILED.value]
    resolved_count = len(done_tasks) + len(failed_tasks)

    success_rate = (len(done_tasks) / resolved_count) if resolved_count > 0 else None

    retry_counts = [(t.retry_count or 0) for t in all_tasks]
    avg_retry_count = (sum(retry_counts) / len(retry_counts)) if retry_counts else 0

    processing_times = []
    for t in done_tasks:
        if t.created_at and t.updated_at:
            delta = (t.updated_at - t.created_at).total_seconds()
            if delta >= 0:
                processing_times.append(delta)
    avg_processing_seconds = (
        sum(processing_times) / len(processing_times) if processing_times else None
    )

    token_summary = log_service.get_token_usage_summary()

    return {
        "total_tasks": len(all_tasks),
        "done_count": len(done_tasks),
        "failed_count": len(failed_tasks),
        "success_rate": success_rate,
        "average_retry_count": round(avg_retry_count, 2),
        "average_processing_seconds": (
            round(avg_processing_seconds, 2) if avg_processing_seconds is not None else None
        ),
        "token_usage": token_summary,
    }


@router.get("/activity")
def live_activity(limit: int = 60):
    """Everything happening right now: a live feed of the most recent log lines
    across every agent, the tasks currently in flight, and today's production +
    spend. This is the dashboard's real-time heartbeat."""
    from config import settings as _s
    feed = _recent_logs(limit=limit, exclude_noise=True)
    running = _running_tasks()
    last_at = feed[0]["at"] if feed else None

    try:
        from app.services.autonomy_service import AutonomyService
        daily = AutonomyService().daily_status()
    except Exception:
        daily = {}
    try:
        from app.services.production_monitor_service import ProductionMonitorService
        prod = ProductionMonitorService().dashboard_summary()
    except Exception:
        prod = {}

    return {
        "polled_at": datetime.utcnow().isoformat(),
        "last_activity_at": last_at,
        "feed": feed,
        "running": running,
        "running_count": len(running),
        "queue_size": task_queue.size(),
        "today": {
            "tasks_created": daily.get("tasks_created"),
            "max_tasks_per_day": daily.get("max_tasks_per_day"),
            "spend_usd": daily.get("spend_usd"),
            "max_daily_spend_usd": daily.get("max_daily_spend_usd"),
            "products_last_24h": prod.get("products_last_24h"),
            "concepts_scored_today": prod.get("concepts_scored_today"),
            "best_score_today": prod.get("best_score_today"),
            "best_concept_today": prod.get("best_concept_today"),
            "blocked_tasks_24h": prod.get("blocked_tasks_24h"),
        },
        "flags": {
            "autonomy_enabled": bool(getattr(_s, "AUTONOMY_ENABLED", False)),
            "auto_publish": bool(getattr(_s, "AUTO_PUBLISH_LISTINGS", False)),
        },
    }


@router.get("/agent/{key}")
def agent_detail(key: str, limit: int = 40):
    """Drill-down for one agent/room: its own recent activity feed (all levels),
    the tasks in flight, and its worker heartbeat — i.e. exactly what it is doing
    right now. `key` is one of the AGENT_GROUPS / room keys."""
    group = AGENT_GROUPS.get(key)
    if not group:
        return {"error": f"unknown agent '{key}'", "known": list(AGENT_GROUPS.keys())}

    feed = _recent_logs(limit=limit, names=group["names"])
    # heartbeat for any worker in this group
    try:
        from app.services import worker_registry
        hbs = worker_registry.get_heartbeats()
        now_utc = datetime.now(timezone.utc)
        heartbeats = {}
        for n in group["names"]:
            last = hbs.get(n)
            if last is not None:
                aware = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
                heartbeats[n] = round((now_utc - aware).total_seconds(), 1)
    except Exception:
        heartbeats = {}

    errors_24h = sum(1 for f in feed if f["level"] in ("ERROR", "CRITICAL"))
    return {
        "key": key,
        "label": group["label"],
        "sources": group["names"],
        "polled_at": datetime.utcnow().isoformat(),
        "last_activity_at": feed[0]["at"] if feed else None,
        "feed": feed,
        "feed_count": len(feed),
        "recent_error_count": errors_24h,
        "heartbeats": heartbeats,
        "running": _running_tasks(),
    }


@router.get("/rooms/status")
def rooms_status():
    from app.services import worker_registry
    from app.db.database import SessionLocal
    from app.models.image_asset import ImageAsset
    from app.models.fulfillment_record import FulfillmentRecord
    from app.models.pod_product import PODProduct
    from app.models.marketing_post import MarketingPost
    from app.models.analytics_event import AnalyticsEvent
    from config import settings as _settings

    db = SessionLocal()
    try:
        # P3-8: two clocks on purpose — now_utc (aware) is compared against
        # tz-aware worker heartbeats; cutoff_24h (naive) is compared against
        # naive DB timestamps (models use datetime.utcnow). Keep them separate.
        now_utc = datetime.now(timezone.utc)
        cutoff_24h = datetime.utcnow() - timedelta(hours=24)

        # ── Task counts ────────────────────────────────────────────
        all_tasks = task_service.list_tasks()
        sc = {s.value: 0 for s in TaskStatus}
        for t in all_tasks:
            if t.status in sc:
                sc[t.status] += 1

        # ── Worker heartbeats ──────────────────────────────────────
        heartbeats = worker_registry.get_heartbeats()

        def _age(name):
            last = heartbeats.get(name)
            if last is None:
                return None
            aware = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
            return round((now_utc - aware).total_seconds(), 1)

        def _wstatus(name, max_age):
            age = _age(name)
            if age is None:
                return "stale"
            return "active" if age <= max_age else "stale"

        # ── DB queries ─────────────────────────────────────────────
        recent_images = (
            db.query(ImageAsset)
            .filter(ImageAsset.created_at >= cutoff_24h)
            .count()
        )
        total_images = db.query(ImageAsset).count()

        recent_fr = (
            db.query(FulfillmentRecord)
            .order_by(FulfillmentRecord.created_at.desc())
            .limit(10)
            .all()
        )
        total_fr = db.query(FulfillmentRecord).count()
        fr_sc = {}
        for r in recent_fr:
            fr_sc[r.status] = fr_sc.get(r.status, 0) + 1
        last_fr = recent_fr[0] if recent_fr else None

        total_pods = db.query(PODProduct).count()

        recent_posts = (
            db.query(MarketingPost)
            .order_by(MarketingPost.created_at.desc())
            .limit(10)
            .all()
        )
        post_sc = {}
        for p in recent_posts:
            post_sc[p.status] = post_sc.get(p.status, 0) + 1

        total_events = db.query(AnalyticsEvent).count()

        # ── Recent errors (filtered per room) ─────────────────────
        all_errors = log_service.list_logs(level="ERROR", limit=30)

        def _errors_for(sources):
            return [
                {
                    "source": e.source,
                    "message": (e.message or "")[:120],
                    "at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in all_errors
                if e.source in sources
            ][:3]

        # ── Feature flags ──────────────────────────────────────────
        auto_publish = bool(getattr(_settings, "AUTO_PUBLISH_LISTINGS", False))
        autonomy_enabled = bool(getattr(_settings, "AUTONOMY_ENABLED", False))

        # ── Rooms ──────────────────────────────────────────────────
        rooms = {
            "research": {
                "label": "Research Room",
                "status": _wstatus("AutonomyWorker", 7200) if autonomy_enabled else "idle",
                "summary": (
                    "Autonomy disabled (kill switch active)"
                    if not autonomy_enabled
                    else f"AutonomyWorker heartbeat {_age('AutonomyWorker')}s ago"
                ),
                "characters": [
                    {
                        "name": "AutonomyWorker",
                        "status": _wstatus("AutonomyWorker", 7200),
                        "heartbeat_age_s": _age("AutonomyWorker"),
                        "detail": f"AUTONOMY_ENABLED={autonomy_enabled}",
                    }
                ],
                "events": _errors_for({"AutonomyWorker", "TrendResearchAgent", "ResearchAgent", "IntelligenceAgent"}),
            },
            "planning": {
                "label": "Planning Room",
                "status": "active" if (sc.get("NEW", 0) + sc.get("PLANNED", 0)) > 0 else "idle",
                "summary": f"{sc.get('NEW', 0)} new, {sc.get('PLANNED', 0)} planned, queue={task_queue.size()}",
                "characters": [
                    {
                        "name": "TaskWorker",
                        "status": _wstatus("TaskWorker", 10),
                        "heartbeat_age_s": _age("TaskWorker"),
                        "detail": f"Queue size: {task_queue.size()}",
                    }
                ],
                "events": _errors_for({"TaskWorker", "TaskProcessor", "TaskService", "TaskQueue", "PlannerAgent"}),
                "counts": {"NEW": sc.get("NEW", 0), "PLANNED": sc.get("PLANNED", 0), "queue": task_queue.size()},
            },
            "content": {
                "label": "Content Room",
                "status": "active" if sc.get("RUNNING", 0) > 0 else "idle",
                "summary": f"{sc.get('RUNNING', 0)} running, {sc.get('DONE', 0)} done, {sc.get('FAILED', 0)} failed",
                "characters": [
                    {
                        "name": "ExecutorAgent",
                        "status": "active" if sc.get("RUNNING", 0) > 0 else "idle",
                        "detail": f"{sc.get('RUNNING', 0)} tasks in RUNNING state",
                    }
                ],
                "events": _errors_for({"ExecutorAgent", "ListingGeneratorAgent", "SEOAgent"}),
                "counts": {"RUNNING": sc.get("RUNNING", 0), "DONE": sc.get("DONE", 0), "FAILED": sc.get("FAILED", 0)},
            },
            "image_studio": {
                "label": "Image Studio",
                "status": "active" if recent_images > 0 else "idle",
                "summary": f"{recent_images} images in last 24h ({total_images} total)",
                "characters": [
                    {
                        "name": "ImageAgents",
                        "status": "active" if recent_images > 0 else "idle",
                        "detail": "ProductImageAgent / SocialImageAgent / PODDesignAgent",
                    }
                ],
                "events": _errors_for({"ProductImageAgent", "SocialImageAgent", "PODDesignAgent", "ImageValidationService", "PipelineOrchestrator", "PDFGenerationService", "MockupService"}),
                "counts": {"last_24h": recent_images, "total": total_images},
            },
            "qa": {
                "label": "QA Room",
                "status": "active" if sc.get("QA", 0) > 0 else "idle",
                "summary": f"{sc.get('QA', 0)} tasks in QA pipeline",
                "characters": [
                    {
                        "name": "QAValidator",
                        "status": "active" if sc.get("QA", 0) > 0 else "idle",
                        "detail": f"{sc.get('QA', 0)} tasks awaiting QA validation",
                    }
                ],
                "events": _errors_for({"QAAgent", "QAValidator", "QARepairAgent"}),
                "counts": {"QA": sc.get("QA", 0)},
            },
            "storefront": {
                "label": "Storefront Room",
                "status": "active" if total_pods > 0 else "idle",
                "summary": f"{total_pods} products, AUTO_PUBLISH={'ON' if auto_publish else 'OFF'}",
                "characters": [
                    {
                        "name": "EtsyListingAgent",
                        "status": "idle",
                        "detail": f"AUTO_PUBLISH_LISTINGS={auto_publish}",
                    }
                ],
                "events": _errors_for({"EtsyClient", "EtsyImageService", "EtsyShippingService", "PipelineOrchestrator"}),
                "counts": {"pod_products": total_pods, "auto_publish": auto_publish},
            },
            "marketing": {
                "label": "Marketing Room",
                "status": (
                    "active" if post_sc.get("success", 0) > 0
                    else "error" if (recent_posts and all(p.status == "failed" for p in recent_posts))
                    else "idle"
                ),
                "summary": (
                    f"{len(recent_posts)} recent posts: {post_sc.get('success', 0)} ok, {post_sc.get('failed', 0)} failed"
                    if recent_posts else "No marketing posts yet"
                ),
                "characters": [
                    {
                        "name": "MarketingService",
                        "status": "active" if post_sc.get("success", 0) > 0 else "idle",
                        "detail": f"{post_sc.get('pending', 0)} pending, {post_sc.get('success', 0)} ok, {post_sc.get('failed', 0)} failed",
                    }
                ],
                "events": _errors_for({"MarketingService", "PinterestChannel", "TumblrChannel", "MarketingRefreshWorker", "PipelineOrchestrator"}),
                "counts": {
                    "pending": post_sc.get("pending", 0),
                    "success": post_sc.get("success", 0),
                    "failed": post_sc.get("failed", 0),
                },
            },
            "fulfillment": {
                "label": "Fulfillment Room",
                "status": _wstatus("EtsyReceiptWorker", 660),
                "summary": (
                    f"{total_fr} records"
                    + (f", last: {last_fr.status} at {last_fr.created_at.strftime('%H:%M')}" if last_fr else "")
                ),
                "characters": [
                    {
                        "name": "EtsyReceiptWorker",
                        "status": _wstatus("EtsyReceiptWorker", 660),
                        "heartbeat_age_s": _age("EtsyReceiptWorker"),
                        "detail": f"{total_fr} total fulfillment records",
                    }
                ],
                "events": _errors_for({"EtsyReceiptWorker", "PODFulfillmentService", "PrintifyClient"}),
                "counts": {"total": total_fr, **fr_sc},
            },
            "ledger": {
                "label": "Ledger Room",
                "status": "active" if total_events > 0 else "idle",
                "summary": f"{total_events} analytics events tracked",
                "characters": [
                    {
                        "name": "AnalyticsService",
                        "status": "active" if total_events > 0 else "idle",
                        "detail": "AnalyticsService / RevenueService / PerformanceService",
                    }
                ],
                "events": _errors_for({"AnalyticsService", "RevenueService", "PerformanceService", "BestProductsService"}),
                "counts": {"total_events": total_events},
            },
        }

        worker_checks = [
            _wstatus("TaskWorker", 10),
            _wstatus("EtsyReceiptWorker", 660),
            _wstatus("AutonomyWorker", 7200),
        ]
        overall_ok = all(s == "active" for s in worker_checks)

        return {
            "polled_at": now_utc.isoformat(),
            "rooms": rooms,
            "workers_overall": {"status": "ok" if overall_ok else "degraded"},
        }
    finally:
        db.close()