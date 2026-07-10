"""
ListingStatsService (STEP 103 A-10) — poll Etsy views + favorites, the earliest
sales signal. Sales are rare early; views/favorites arrive ~100x sooner and tell
you which products/keywords Etsy is actually showing to buyers. This data powers
PerformanceService's engagement score and the A-1 learning loop.

getListingsByShop returns views + num_favorers for 100 listings per call — the
whole shop is 1-5 calls/day. Everything is best-effort.
"""
import logging

import httpx

from app.db.database import SessionLocal
from app.services.analytics_service import AnalyticsService
from app.services.etsy_oauth import get_valid_access_token
from config import settings

logger = logging.getLogger("ai-factory")

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


class ListingStatsService:
    def __init__(self):
        self.analytics = AnalyticsService()

    async def _fetch_active_listings(self) -> list:
        if not settings.ETSY_SHOP_ID:
            return []
        at = await get_valid_access_token()
        headers = {"Authorization": f"Bearer {at}",
                   "x-api-key": f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"}
        listings, offset = [], 0
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                r = await client.get(
                    f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/active",
                    headers=headers, params={"limit": 100, "offset": offset},
                )
                if r.status_code >= 400:
                    logger.warning(f"ListingStatsService: {r.status_code}: {r.text[:150]}")
                    break
                page = r.json().get("results", [])
                listings.extend(page)
                if len(page) < 100:
                    break
                offset += 100
        return listings

    @staticmethod
    def _resolve_task_id(listing_id: str):
        """Map an Etsy listing_id to the task that generated it (digital via
        ImageAsset, POD via PODProduct)."""
        from app.models.image_asset import ImageAsset
        from app.models.pod_product import PODProduct
        db = SessionLocal()
        try:
            asset = db.query(ImageAsset).filter(ImageAsset.listing_id == str(listing_id)).first()
            if asset:
                return asset.task_id
            pod = db.query(PODProduct).filter(PODProduct.etsy_listing_id == str(listing_id)).first()
            return pod.task_id if pod else None
        finally:
            db.close()

    def record_stats(self, listings: list) -> int:
        """Record a listing_stats analytics event per listing that maps to a
        task. Returns the number recorded."""
        recorded = 0
        for listing in listings:
            listing_id = str(listing.get("listing_id", ""))
            if not listing_id:
                continue
            views = int(listing.get("views", 0) or 0)
            favorites = int(listing.get("num_favorers", 0) or 0)
            task_id = self._resolve_task_id(listing_id)
            if not task_id:
                continue
            self.analytics.record_event(
                event_type="listing_stats",
                entity_type="task",
                entity_id=task_id,
                value=float(views + 10 * favorites),
                payload={"listing_id": listing_id, "views": views, "favorites": favorites},
            )
            recorded += 1
        return recorded

    def poll_and_record(self) -> dict:
        """Fetch active listings and record their stats. Best-effort."""
        try:
            import asyncio
            listings = asyncio.run(self._fetch_active_listings())
        except Exception as e:
            logger.error(f"ListingStatsService: fetch failed: {e}")
            return {"ok": False, "error": str(e)}
        recorded = self.record_stats(listings)
        logger.info(f"ListingStatsService: recorded stats for {recorded}/{len(listings)} listings")
        return {"ok": True, "listings": len(listings), "recorded": recorded}
