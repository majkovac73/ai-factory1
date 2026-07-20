"""
Audit 2026-07-20 #3 — enforce-mode zero-passer streak guardrail.

When PRODUCT_SCORE_ENFORCE is on and N consecutive autonomy cycles produce no
passing concept, the factory is silently building nothing. ProductionMonitorService
.record_enforce_cycle_outcome() must count the streak and alert exactly when it
reaches PRODUCT_ENFORCE_ZERO_STREAK_ALERT (and reset on a produced cycle).

Usage: python scripts/test_audit_step3_enforce_streak.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import production_monitor_service as pms
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def make_svc(tmpdir):
    svc = pms.ProductionMonitorService()
    svc._streak_marker = lambda: Path(tmpdir) / "streak.json"
    svc.concept_stats_today = lambda: {"near_miss_total": 72}
    return svc


with tempfile.TemporaryDirectory() as tmp:
    alerts = []
    fake_alert = MagicMock()
    fake_alert.return_value.send_alert_sync = lambda *a, **k: alerts.append(a)

    with patch.object(settings, "PRODUCT_SCORE_ENFORCE", True), \
         patch.object(settings, "PRODUCT_ENFORCE_ZERO_STREAK_ALERT", 3), \
         patch("app.services.alert_service.AlertService", fake_alert):
        svc = make_svc(tmp)
        r1 = svc.record_enforce_cycle_outcome(produced=False)
        r2 = svc.record_enforce_cycle_outcome(produced=False)
        check("streak counts up (1,2)", r1["streak"] == 1 and r2["streak"] == 2)
        check("no alert before threshold", len(alerts) == 0)
        r3 = svc.record_enforce_cycle_outcome(produced=False)
        check("streak reaches 3", r3["streak"] == 3)
        check("alert fires exactly at threshold", r3["alerted"] and len(alerts) == 1)
        r4 = svc.record_enforce_cycle_outcome(produced=False)
        check("no repeat alert after threshold", not r4["alerted"] and len(alerts) == 1)
        r5 = svc.record_enforce_cycle_outcome(produced=True)
        check("produced cycle resets streak", r5["streak"] == 0)

with tempfile.TemporaryDirectory() as tmp:
    alerts = []
    fake_alert = MagicMock()
    fake_alert.return_value.send_alert_sync = lambda *a, **k: alerts.append(a)
    with patch.object(settings, "PRODUCT_SCORE_ENFORCE", False), \
         patch("app.services.alert_service.AlertService", fake_alert):
        svc = make_svc(tmp)
        r = svc.record_enforce_cycle_outcome(produced=False)
        check("enforce OFF -> no tracking/alert", r["streak"] == 0 and not r["alerted"] and len(alerts) == 0)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#3 enforce-streak tests passed.")
