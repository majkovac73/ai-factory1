"""
ListingPruneService (STEP 103 C-5) — flag/deactivate dead inventory.

At 10 listings/day, auto-renew fees ($0.20 every ~4 months) pile up on a growing
heap of zero-view listings, and a shop full of unsold listings drags perceived
quality. This finds active listings older than LISTING_PRUNE_MIN_AGE_DAYS with
ZERO recorded sales and views <= LISTING_PRUNE_MAX_VIEWS. DRY-RUN by default —
it reports candidates (and alerts Discord) so a human decides; apply=True
deactivates them via update_listing(state="inactive").
"""
import logging
import time

from app.services.analytics_service import AnalyticsService
from app.services.listing_stats_service import ListingStatsService
from config import settings

logger = logging.getLogger("ai-factory")


class ListingPruneService:
    def __init__(self):
        self.analytics = AnalyticsService()

    def _has_sales(self, task_id: str) -> bool:
        if not task_id:
            return False
        events = self.analytics.get_events(event_type="sale_recorded", entity_type="task",
                                           entity_id=task_id, limit=5)
        return len(events) > 0

    def run(self, apply: bool = False) -> dict:
        import asyncio
        try:
            listings = asyncio.run(ListingStatsService()._fetch_active_listings())
        except Exception as e:
            logger.error(f"ListingPruneService: fetch failed: {e}")
            return {"ok": False, "error": str(e)}

        now = time.time()
        min_age = getattr(settings, "LISTING_PRUNE_MIN_AGE_DAYS", 100) * 86400
        max_views = getattr(settings, "LISTING_PRUNE_MAX_VIEWS", 10)

        candidates = []
        for listing in listings:
            listing_id = str(listing.get("listing_id", ""))
            created = listing.get("created_timestamp") or listing.get("creation_tsz") or 0
            views = int(listing.get("views", 0) or 0)
            age_days = (now - int(created)) / 86400 if created else 0
            if age_days < min_age / 86400 or views > max_views:
                continue
            task_id = ListingStatsService._resolve_task_id(listing_id)
            if self._has_sales(task_id):
                continue
            candidates.append({
                "listing_id": listing_id,
                "title": listing.get("title", ""),
                "age_days": round(age_days, 1),
                "views": views,
            })

        deactivated = 0
        if apply and candidates:
            from app.services.etsy_client import EtsyClient
            for c in candidates:
                try:
                    asyncio.run(EtsyClient().update_listing(c["listing_id"], {"state": "inactive"}))
                    deactivated += 1
                except Exception as e:
                    logger.error(f"ListingPruneService: deactivate {c['listing_id']} failed: {e}")

        report = {"ok": True, "active": len(listings), "candidates": candidates,
                  "deactivated": deactivated, "applied": apply}
        # Alert (dry-run reporting) so a human sees the list before trusting it.
        if candidates:
            try:
                from app.services.alert_service import AlertService
                verb = "Deactivated" if apply else "Prune candidates (dry-run)"
                AlertService().send_alert_sync(
                    f"Listing pruning — {verb}: {len(candidates)}",
                    "Stale zero-sale, low-view listings: "
                    + ", ".join(f"{c['listing_id']} ({c['views']}v, {c['age_days']}d)" for c in candidates[:20]),
                    level="warning",
                )
            except Exception:
                pass
        logger.info(f"ListingPruneService: {len(candidates)} candidates, deactivated={deactivated}, applied={apply}")
        return report
