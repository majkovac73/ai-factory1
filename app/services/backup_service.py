"""
BackupService (STEP 103 C-3) — protect the business's irreplaceable state.

The SQLite DB holds OAuth tokens (manual re-auth if lost), PODProduct↔listing
mappings (fulfillment breaks without them — paying customers affected), the
image catalog, and every analytics/revenue event the learning loop depends on.
A single Railway volume failure or accidental deletion wipes all of it.

This creates a timestamped zip of a CONSISTENT DB copy (sqlite3 online backup)
plus the runtime state JSONs, and either uploads it to an S3-compatible bucket
(Cloudflare R2 / Backblaze B2, configured via env) or keeps the last N zips on
the volume as a fallback. Images are excluded (regenerable at cost); the DB
cannot be regenerated.
"""
import logging
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.paths import get_data_dir
from config import settings

logger = logging.getLogger("ai-factory")


class BackupService:
    def __init__(self):
        self.data_dir = get_data_dir()
        self.backup_dir = self.data_dir / "backups"

    def _db_path(self) -> Path:
        # Same resolution as app/db/database.py (DATABASE_PATH wins, else default).
        db_path = os.getenv("DATABASE_PATH")
        if db_path:
            return Path(db_path)
        try:
            from app.db.database import engine
            if engine.url.database:
                return Path(engine.url.database)
        except Exception:
            pass
        return self.data_dir.parent / "app.db"

    def offsite_configured(self) -> bool:
        return bool(
            settings.BACKUP_S3_BUCKET
            and settings.BACKUP_S3_ACCESS_KEY_ID
            and settings.BACKUP_S3_SECRET_ACCESS_KEY
        )

    def create_backup(self) -> dict:
        """Create one backup zip; upload off-box if configured, else keep locally.
        Returns a small report dict. Never raises for a routine failure — logs
        and returns {ok: False}."""
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            zip_path = self.backup_dir / f"factory_backup_{ts}.zip"

            # 1. Consistent DB snapshot via SQLite's online backup API.
            db_snapshot = self.backup_dir / f"app_{ts}.db"
            self._snapshot_db(db_snapshot)

            # 2. Zip DB snapshot + runtime state JSONs (not images).
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(db_snapshot, arcname="app.db")
                for state_json in sorted(self.data_dir.glob("*.json")):
                    try:
                        zf.write(state_json, arcname=state_json.name)
                    except Exception as e:
                        logger.warning(f"BackupService: could not add {state_json.name}: {e}")
            db_snapshot.unlink(missing_ok=True)

            size = zip_path.stat().st_size
            report = {"ok": True, "file": zip_path.name, "bytes": size, "offsite": False}

            # 3. Offsite upload if configured; else keep last-N local + weekly warn.
            if self.offsite_configured():
                uploaded = self._upload_offsite(zip_path)
                report["offsite"] = uploaded
                if not uploaded:
                    report["ok"] = False
            self._prune_local()

            logger.info(f"BackupService: backup created {zip_path.name} ({size} bytes), offsite={report['offsite']}")
            return report
        except Exception as e:
            logger.error(f"BackupService: backup FAILED: {e}")
            return {"ok": False, "error": str(e)}

    def _snapshot_db(self, dest: Path) -> None:
        src_path = self._db_path()
        if not src_path.exists():
            raise FileNotFoundError(f"DB not found at {src_path}")
        src = sqlite3.connect(str(src_path))
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)  # online, consistent even under concurrent writes
            finally:
                dst.close()
        finally:
            src.close()

    def _upload_offsite(self, zip_path: Path) -> bool:
        try:
            import boto3  # lazy — only needed when offsite is configured
        except Exception:
            logger.error("BackupService: BACKUP_S3_* configured but boto3 is not installed")
            return False
        try:
            client = boto3.client(
                "s3",
                endpoint_url=settings.BACKUP_S3_ENDPOINT_URL,
                aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY_ID,
                aws_secret_access_key=settings.BACKUP_S3_SECRET_ACCESS_KEY,
                region_name=settings.BACKUP_S3_REGION,
            )
            client.upload_file(str(zip_path), settings.BACKUP_S3_BUCKET, f"backups/{zip_path.name}")
            return True
        except Exception as e:
            logger.error(f"BackupService: offsite upload failed: {e}")
            return False

    def _prune_local(self) -> None:
        keep = max(1, getattr(settings, "BACKUP_KEEP_LOCAL", 7))
        zips = sorted(self.backup_dir.glob("factory_backup_*.zip"))
        for old in zips[:-keep]:
            try:
                old.unlink()
            except Exception:
                pass
