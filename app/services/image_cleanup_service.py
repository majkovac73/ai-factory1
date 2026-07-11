"""
ImageCleanupService (STEP 103 disk hygiene) — keep data/images from filling the
Railway volume.

Generated listing mockups are transient: the pipeline creates them, uploads them
to Etsy, and never reads them again. Delivery files are hosted by Etsy once the
listing is published (and by Printify for POD). Without pruning, data/images
grows unbounded — and A-5's multi-ratio bundle multiplies files per product.

This prunes:
  - data/images/listing/**   older than LISTING_MAX_AGE_HOURS
  - data/images/delivery/**  older than DELIVERY_MAX_AGE_DAYS
It never touches data/images/scenes (the reused P3-6 scene cache). Recent files
are kept so in-flight / resumable tasks (P0-9, 6h window) still have their assets.
"""
import logging
import time
from pathlib import Path

from app.core.paths import get_data_dir
from config import settings

logger = logging.getLogger("ai-factory")


class ImageCleanupService:
    def __init__(self):
        self.images_dir = get_data_dir() / "images"

    def cleanup(self) -> dict:
        if not getattr(settings, "IMAGE_CLEANUP_ENABLED", True):
            return {"ok": True, "skipped": "disabled"}
        listing_max_age = getattr(settings, "IMAGE_CLEANUP_LISTING_MAX_AGE_HOURS", 6) * 3600
        delivery_max_age = getattr(settings, "IMAGE_CLEANUP_DELIVERY_MAX_AGE_DAYS", 3) * 86400

        deleted = 0
        freed = 0
        for sub, max_age in (("listing", listing_max_age), ("delivery", delivery_max_age)):
            d, f = self._prune(self.images_dir / sub, max_age)
            deleted += d
            freed += f

        report = {"ok": True, "deleted_files": deleted, "freed_bytes": freed}
        logger.info(f"ImageCleanupService: {report}")
        return report

    @staticmethod
    def _prune(root: Path, max_age_seconds: float):
        if not root.exists():
            return 0, 0
        now = time.time()
        deleted = 0
        freed = 0
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                if now - p.stat().st_mtime > max_age_seconds:
                    freed += p.stat().st_size
                    p.unlink()
                    deleted += 1
            except Exception:
                pass
        # remove now-empty subdirectories
        for d in sorted(root.rglob("*"), reverse=True):
            try:
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            except Exception:
                pass
        return deleted, freed
