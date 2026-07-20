"""
Audit 2026-07-20 #11 — blocked tasks are surfaced (counted + daily alert).

A pipeline block persists output_data.pipeline_status='BLOCKED_NO_PRODUCT' (not a
deletion, and task.status stays DONE because the task's own QA passed). Previously
nothing counted/alerted on these, so silent-failure regressions were invisible.
ProductionMonitorService now counts them and alerts once/day.

Usage: python scripts/test_audit_step11_blocked_tasks.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "b.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.services.production_monitor_service import ProductionMonitorService

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


db = SessionLocal()
db.add(Task(id="t-blocked-1", prompt="p", type="coloring_page", status="DONE",
            output_data={"pipeline_status": "BLOCKED_NO_PRODUCT",
                         "pipeline_blocked_reason": "digital file upload failed: 500"}))
db.add(Task(id="t-blocked-2", prompt="p", type="coloring_page", status="DONE",
            output_data={"pipeline_status": "BLOCKED_NO_PRODUCT",
                         "pipeline_blocked_reason": "digital file upload failed: timeout"}))
db.add(Task(id="t-ok", prompt="p", type="coloring_page", status="DONE",
            output_data={"pipeline_status": "COMPLETED", "listing_id": "123"}))
db.commit()
db.close()

pms = ProductionMonitorService()
info = pms.blocked_tasks(24)
check("counts both blocked tasks", info["count"] == 2)
check("excludes COMPLETED task", "t-ok" not in info["task_ids"])
check("buckets top reasons by leading phrase",
      info["top_reasons"] and info["top_reasons"][0][0] == "digital file upload failed" and info["top_reasons"][0][1] == 2)

# daily alert fires once
alerts = []
fake_alert = MagicMock()
fake_alert.return_value.send_alert_sync = lambda *a, **k: alerts.append(a)
with tempfile.TemporaryDirectory() as tmp:
    with patch("app.core.paths.get_data_dir", return_value=__import__("pathlib").Path(tmp)), \
         patch("app.services.alert_service.AlertService", fake_alert):
        r1 = pms.run_blocked_tasks_check()
        r2 = pms.run_blocked_tasks_check()
check("daily alert fires", len(alerts) == 1)
check("first run alerted True", r1["alerted"] is True)
check("second run suppressed", r2["alerted"] != True)

# dashboard summary includes blocked count
summ = pms.dashboard_summary()
check("dashboard exposes blocked_tasks_24h", summ.get("blocked_tasks_24h") == 2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#11 blocked-task tests passed.")
