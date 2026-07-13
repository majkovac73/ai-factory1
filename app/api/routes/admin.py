"""
Admin routes (STEP 103 C-3). Protected by the FACTORY_API_KEY middleware
(all POSTs require X-Factory-Key).
"""
import logging

from fastapi import APIRouter

logger = logging.getLogger("ai-factory")

router = APIRouter()


@router.post("/backup")
def run_backup():
    """Manually trigger a database + state backup (C-3). Returns the backup
    report (file name, size, whether it was uploaded off-box)."""
    from app.services.backup_service import BackupService
    return BackupService().create_backup()


@router.post("/cleanup")
def run_image_cleanup():
    """Manually prune old generated images (disk hygiene). Returns counts."""
    from app.services.image_cleanup_service import ImageCleanupService
    return ImageCleanupService().cleanup()


@router.post("/prune-listings")
def prune_listings(apply: bool = False):
    """C-5: report (dry-run, default) or deactivate (apply=true) stale zero-sale,
    low-view listings. Always alerts Discord with the candidate list."""
    from app.services.listing_prune_service import ListingPruneService
    return ListingPruneService().run(apply=apply)


@router.post("/pinterest-backfill")
def pinterest_backfill(apply: bool = False, limit: int = 50, sleep_seconds: float = 3.0, rewrite: bool = True):
    """Pin the whole past catalog to Pinterest (products never pinned before).
    apply=false (default) returns the PLAN synchronously (posts nothing);
    apply=true launches the posting in a background thread (it takes minutes with
    the per-post delay) and returns immediately. Re-runs skip already-pinned
    products. Requires Pinterest connected."""
    from app.services.pinterest_backfill_service import PinterestBackfillService
    svc = PinterestBackfillService()
    if not apply:
        return svc.run(apply=False, limit=limit, sleep_seconds=sleep_seconds, rewrite_caption=rewrite)

    import threading
    cands = svc.candidates()
    to_post = min(len(cands), limit)

    def _run():
        try:
            rep = svc.run(apply=True, limit=limit, sleep_seconds=sleep_seconds, rewrite_caption=rewrite)
            logger.info(f"pinterest-backfill: done — {rep.get('posted')}/{rep.get('to_post')} posted")
        except Exception as e:
            logger.error(f"pinterest-backfill: failed — {e}")

    threading.Thread(target=_run, daemon=True, name="PinterestBackfill").start()
    return {"started": True, "to_post": to_post, "total_candidates": len(cands),
            "note": "posting in the background (~%.0fs/post); watch the logs / your board." % sleep_seconds}
