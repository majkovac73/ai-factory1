"""
Step 103 / C-3 test — backups (consistent DB snapshot + state, restore drill,
local pruning, offsite-configured detection).

  [1] create_backup produces a zip containing app.db + the state JSONs.
  [2] RESTORE DRILL: the DB inside the zip opens and its rows are readable
      (proves the snapshot is a valid, consistent copy).
  [3] _prune_local keeps only BACKUP_KEEP_LOCAL zips.
  [4] offsite_configured reflects the BACKUP_S3_* env.

Usage: python scripts/test_step103_backup.py
"""
import io
import os
import sys
import tempfile
import zipfile
from unittest.mock import patch

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "app.db")  # data dir = _tmp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from config import settings
from app.services.backup_service import BackupService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)
# Seed a row so the restore drill has something to count.
db = SessionLocal()
db.add(Task(id="t-backup", prompt="p", type="single_print", status="DONE", input_data={}))
db.commit()
db.close()

# a state json in the data dir
with open(os.path.join(_tmp, "receipt_worker_state.json"), "w") as f:
    f.write('{"last_checked_at": 123}')

svc = BackupService()

# [1] create
with patch.object(settings, "BACKUP_S3_BUCKET", None):
    report = svc.create_backup()
check("1 backup ok", report.get("ok") is True)
zip_path = svc.backup_dir / report["file"]
check("1 zip exists", zip_path.exists())
with zipfile.ZipFile(zip_path) as zf:
    names = zf.namelist()
check("1 zip contains app.db", "app.db" in names)
check("1 zip contains state json", "receipt_worker_state.json" in names)

# [2] restore drill — open the DB from the zip, count tasks
import sqlite3
restore_path = os.path.join(_tmp, "restored.db")
with zipfile.ZipFile(zip_path) as zf:
    with zf.open("app.db") as src, open(restore_path, "wb") as dst:
        dst.write(src.read())
conn = sqlite3.connect(restore_path)
cur = conn.execute("SELECT COUNT(*) FROM tasks WHERE id='t-backup'")
count = cur.fetchone()[0]
conn.close()
check("2 restore drill: seeded row present in backed-up DB", count == 1)

# [3] pruning
with patch.object(settings, "BACKUP_KEEP_LOCAL", 3), patch.object(settings, "BACKUP_S3_BUCKET", None):
    for _ in range(5):
        import time; time.sleep(1.05)  # distinct second-resolution timestamps
        svc.create_backup()
remaining = list(svc.backup_dir.glob("factory_backup_*.zip"))
check("3 prune keeps only BACKUP_KEEP_LOCAL zips", len(remaining) == 3)

# [4] offsite detection
with patch.object(settings, "BACKUP_S3_BUCKET", None):
    check("4 offsite not configured", svc.offsite_configured() is False)
with patch.object(settings, "BACKUP_S3_BUCKET", "b"), \
     patch.object(settings, "BACKUP_S3_ACCESS_KEY_ID", "k"), \
     patch.object(settings, "BACKUP_S3_SECRET_ACCESS_KEY", "s"):
    check("4 offsite configured when all set", svc.offsite_configured() is True)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 backup tests passed.")
