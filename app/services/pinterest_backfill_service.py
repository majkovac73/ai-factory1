"""
PinterestBackfillService — one-time (repeatable) promotion of the ENTIRE past
catalog to Pinterest.

The recurring MarketingRefreshService rotates a few products per cycle across all
channels. This instead sweeps EVERY published product that has never had a
successful Pinterest post and pins it, so a freshly-connected Pinterest account
gets seeded with the whole back-catalog. It reuses MarketingRefreshService for
candidate resolution, asset selection, and posting (which records a MarketingPost,
so re-runs skip what's already pinned).

Safety:
  - dry-run by default (apply=False) — returns the plan, posts nothing;
  - bounded by `limit` posts per run;
  - a per-post delay (`sleep_seconds`) so a burst can't trip Pinterest rate limits;
  - only PUBLISHED products (real etsy_listing_id) with a local asset on disk;
  - skips products already successfully pinned (idempotent).
"""
import logging
import time
from typing import Optional

from app.db.database import SessionLocal
from app.models.marketing_post import MarketingPost
from app.services.marketing_refresh_service import MarketingRefreshService

logger = logging.getLogger("ai-factory")


class PinterestBackfillService:
    def __init__(self, refresh: Optional[MarketingRefreshService] = None):
        self.refresh = refresh or MarketingRefreshService()

    @staticmethod
    def _already_pinned(task_id: str) -> bool:
        db = SessionLocal()
        try:
            return db.query(MarketingPost).filter(
                MarketingPost.task_id == task_id,
                MarketingPost.channel == "pinterest",
                MarketingPost.status == "success",
            ).first() is not None
        finally:
            db.close()

    def candidates(self, include_already_pinned: bool = False) -> list:
        """Every published product not yet pinned (task_id, listing_id, title)."""
        from app.db.database import SessionLocal as SL
        from app.models.task import Task
        from app.schemas.enums import TaskStatus
        from app.core.product_formats import PRODUCT_FORMATS
        db = SL()
        try:
            tasks = (db.query(Task)
                     .filter(Task.status == TaskStatus.DONE.value, Task.type.in_(list(PRODUCT_FORMATS.keys())))
                     .order_by(Task.created_at.asc()).all())
        finally:
            db.close()
        out = []
        for t in tasks:
            listing_id = self.refresh.resolve_listing_id(t.id)
            if not listing_id:
                continue  # not genuinely published (or was blocked)
            if not include_already_pinned and self._already_pinned(t.id):
                continue
            title = (t.output_data or {}).get("title") or t.title or "Product"
            out.append({"task_id": t.id, "listing_id": listing_id, "title": title})
        return out

    def run(self, apply: bool = False, limit: int = 50, sleep_seconds: float = 3.0,
            rewrite_caption: bool = True, include_already_pinned: bool = False) -> dict:
        from app.services.pinterest_oauth import can_publish
        from app.marketing.pinterest_channel import PinterestChannel

        cands = self.candidates(include_already_pinned=include_already_pinned)
        plan = cands[:limit]
        report = {"total_candidates": len(cands), "to_post": len(plan), "applied": apply, "posted": 0, "results": []}

        if not apply:
            report["dry_run"] = [{"task_id": c["task_id"], "title": c["title"][:70]} for c in plan]
            return report

        if not can_publish():
            report["error"] = "Pinterest cannot publish (not connected, or Trial-blocked — set PINTEREST_CAN_PUBLISH once Standard access is granted)"
            return report

        channel = PinterestChannel()
        for i, c in enumerate(plan):
            res = self.refresh.refresh_post(c["task_id"], channel, listing_id=c["listing_id"],
                                            rewrite_caption=rewrite_caption)
            report["results"].append({"task_id": c["task_id"], "title": c["title"][:60],
                                      "success": res.get("success"), "url": res.get("url"), "error": res.get("error")})
            if res.get("success"):
                report["posted"] += 1
            if sleep_seconds and i < len(plan) - 1:
                time.sleep(sleep_seconds)
        logger.info(f"PinterestBackfillService: posted {report['posted']}/{len(plan)} (of {len(cands)} candidates)")
        return report
