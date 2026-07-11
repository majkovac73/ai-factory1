"""
SeasonalListingService (STEP 104 1-4) — bring seasonal listings DOWN when their
occasion window closes, and back UP when it reopens next year.

A Valentine's listing built in January otherwise stays active all year: a shop
grid full of dead-season products depresses conversion on EVERY listing, keeps
paying $0.20 auto-renew, and drags Etsy's shop-quality signal. Deactivating
(state="inactive") and later REACTIVATING (state="active") keeps the listing's
accumulated age/views/favorites, which beats rebuilding it from scratch.

We track our own action in task metadata ("seasonal_state") so we don't hammer
the Etsy API re-sending the same state each tick.
"""
import logging

from app.core.product_formats import PRODUCT_FORMATS
from app.core.seasonality import occasion_in_window
from app.db.database import SessionLocal
from app.models.task import Task
from app.schemas.enums import TaskStatus

logger = logging.getLogger("ai-factory")


class SeasonalListingService:
    def _resolve_listing_id(self, task_id: str):
        from app.models.image_asset import ImageAsset
        from app.models.pod_product import PODProduct
        db = SessionLocal()
        try:
            a = db.query(ImageAsset).filter(ImageAsset.task_id == task_id, ImageAsset.listing_id.isnot(None)).first()
            if a:
                return a.listing_id
            p = db.query(PODProduct).filter(PODProduct.task_id == task_id, PODProduct.etsy_listing_id.isnot(None)).first()
            return p.etsy_listing_id if p else None
        finally:
            db.close()

    def _set_seasonal_state(self, task_id: str, state: str):
        db = SessionLocal()
        try:
            t = db.query(Task).filter(Task.id == task_id).first()
            if t:
                t.metadata_ = {**(t.metadata_ or {}), "seasonal_state": state}
                db.commit()
        finally:
            db.close()

    def run(self, apply: bool = True) -> dict:
        """Deactivate out-of-window seasonal listings, reactivate in-window ones."""
        import asyncio
        db = SessionLocal()
        try:
            tasks = (
                db.query(Task)
                .filter(Task.status == TaskStatus.DONE.value, Task.type.in_(list(PRODUCT_FORMATS.keys())))
                .all()
            )
            seasonal = [(t.id, (t.metadata_ or {}).get("occasion"), (t.metadata_ or {}).get("seasonal_state", "active"))
                        for t in tasks if (t.metadata_ or {}).get("occasion")]
        finally:
            db.close()

        deactivated, reactivated = [], []
        from app.services.etsy_client import EtsyClient
        for task_id, occ, current in seasonal:
            listing_id = self._resolve_listing_id(task_id)
            if not listing_id:
                continue
            in_window = occasion_in_window(occ)
            if not in_window and current == "active":
                if apply:
                    try:
                        asyncio.run(EtsyClient().update_listing(str(listing_id), {"state": "inactive"}))
                        self._set_seasonal_state(task_id, "inactive")
                    except Exception as e:
                        logger.error(f"SeasonalListingService: deactivate {listing_id} failed: {e}")
                        continue
                deactivated.append(listing_id)
            elif in_window and current == "inactive":
                if apply:
                    try:
                        asyncio.run(EtsyClient().update_listing(str(listing_id), {"state": "active"}))
                        self._set_seasonal_state(task_id, "active")
                    except Exception as e:
                        logger.error(f"SeasonalListingService: reactivate {listing_id} failed: {e}")
                        continue
                reactivated.append(listing_id)

        report = {"ok": True, "seasonal_listings": len(seasonal),
                  "deactivated": len(deactivated), "reactivated": len(reactivated)}
        logger.info(f"SeasonalListingService: {report}")
        return report
