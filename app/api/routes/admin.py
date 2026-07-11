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
