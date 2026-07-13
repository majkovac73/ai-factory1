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


@router.post("/pinterest-diagnose")
def pinterest_diagnose():
    """One-shot Pinterest write diagnostic: shows the REAL Pinterest response for
    a user-account read and a single pin-create attempt (status + body), so a
    401/403 on pin creation reveals its actual cause (scope, trial access, board
    ownership, media). Temporary debug aid."""
    import asyncio
    import base64
    import pathlib

    import httpx

    from app.services.pinterest_oauth import get_valid_access_token
    from app.services.pinterest_backfill_service import PinterestBackfillService
    from app.services.marketing_refresh_service import MarketingRefreshService
    from config import settings

    async def _run():
        out = {"board_id": settings.PINTEREST_BOARD_ID}
        try:
            token = await get_valid_access_token()
            out["token_prefix"] = (token or "")[:10]
        except Exception as e:
            out["token_error"] = str(e)
            return out
        async with httpx.AsyncClient(timeout=30) as client:
            ua = await client.get("https://api.pinterest.com/v5/user_account",
                                  headers={"Authorization": f"Bearer {token}"})
            out["user_account_status"] = ua.status_code
            out["user_account_body"] = ua.text[:400]

            cands = PinterestBackfillService().candidates()
            if not cands:
                out["pin_attempt"] = "no candidates"
                return out
            c = cands[0]
            asset = MarketingRefreshService()._pick_asset_path(c["task_id"])
            out["asset_used"] = asset
            payload = {
                "board_id": settings.PINTEREST_BOARD_ID,
                "title": c["title"][:100],
                "description": "diagnostic",
                "link": f"https://www.etsy.com/listing/{c['listing_id']}",
            }
            if asset and pathlib.Path(asset).exists() and str(asset).lower().endswith((".png", ".jpg", ".jpeg")):
                data = base64.b64encode(pathlib.Path(asset).read_bytes()).decode()
                payload["media_source"] = {"source_type": "image_base64",
                                           "content_type": "image/png", "data": data}
            else:
                out["asset_note"] = "no PNG/JPG asset; attempting pin WITHOUT media (will show a media error, not an auth error, if auth is fine)"
            r = await client.post("https://api.pinterest.com/v5/pins",
                                  headers={"Authorization": f"Bearer {token}"}, json=payload)
            out["pin_status"] = r.status_code
            out["pin_body"] = r.text[:800]
        return out

    return asyncio.run(_run())


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
