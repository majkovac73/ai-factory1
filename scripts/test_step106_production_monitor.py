"""
Step 106-D test — 1-9 zero-production monitor + tile, 1-10 approve-concept.

Usage: python scripts/test_step106_production_monitor.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s106d.db")
_data = tempfile.mkdtemp()
os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(_data, "images")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.models.analytics_event import AnalyticsEvent  # noqa
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


from app.services.production_monitor_service import ProductionMonitorService
from app.services.analytics_service import AnalyticsService

pm = ProductionMonitorService()

# ── 1-9: products_created counts product tasks in window ──
db = SessionLocal()
db.add(Task(id="p1", prompt="p", type="single_print", status="DONE", input_data={},
            metadata_={"source": "autonomy_worker", "product_name": "A"}, created_at=datetime.utcnow()))
db.add(Task(id="old", prompt="p", type="single_print", status="DONE", input_data={},
            metadata_={"source": "autonomy_worker", "product_name": "B"},
            created_at=datetime.utcnow() - timedelta(days=3)))
db.commit(); db.close()

check("1-9 products_created(24h) counts recent product task", pm.products_created(24) == 1)
check("1-9 products_created(7d) counts both", pm.products_created(24 * 7) == 2)

# ── 1-9: zero-production alert fires when autonomy on + 0 built ──
pm2 = ProductionMonitorService()
alerts = []
with patch.object(settings, "AUTONOMY_ENABLED", True), \
     patch.object(ProductionMonitorService, "products_created", return_value=0), \
     patch("app.services.alert_service.AlertService") as AS:
    AS.return_value.send_alert_sync.side_effect = lambda *a, **k: alerts.append(a)
    rep = pm2.run_zero_production_check()
check("1-9 alert fires on zero production", rep.get("alerted") is True and len(alerts) == 1)
check("1-9 alert message names zero production", "0 products" in alerts[0][0].lower() or "0 products" in alerts[0][1].lower())

# once-per-day suppression
with patch.object(settings, "AUTONOMY_ENABLED", True), \
     patch.object(ProductionMonitorService, "products_created", return_value=0), \
     patch("app.services.alert_service.AlertService") as AS2:
    AS2.return_value.send_alert_sync.side_effect = lambda *a, **k: alerts.append(a)
    rep2 = pm2.run_zero_production_check()
check("1-9 alert suppressed second time same day", rep2.get("alerted") != True)

# autonomy off -> no alert
with patch.object(settings, "AUTONOMY_ENABLED", False):
    rep3 = pm2.run_zero_production_check()
check("1-9 no alert when autonomy disabled", rep3.get("alerted") is False)

# dashboard summary shape
summary = pm2.dashboard_summary()
check("1-9 dashboard summary has the tile fields",
      {"products_last_24h", "products_last_7d", "concepts_scored_today", "best_score_today"} <= set(summary))

# ── 1-10: approve-concept endpoint creates a manual_approval task ──
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)

concept = {"product_name": "Retro Sunset Gradient Print", "product_format": "single_print",
           "description": "A retro sunset gradient wall art print.",
           "market": {"price_p50": 6.0, "top_titles": ["retro sunset print"]}}
r = client.post("/tasks/approve-concept", json=concept)
check("1-10 approve-concept returns 200", r.status_code == 200)
body = r.json()
check("1-10 task created with the concept format", body.get("type") == "single_print")
check("1-10 task tagged source=manual_approval",
      (body.get("metadata") or {}).get("source") == "manual_approval")
check("1-10 market seo_context carried through",
      "retro sunset print" in ((body.get("metadata") or {}).get("seo_context") or []))

# validation: missing fields -> 400
r2 = client.post("/tasks/approve-concept", json={"product_name": "x"})
check("1-10 rejects incomplete concept (400)", r2.status_code == 400)
r3 = client.post("/tasks/approve-concept", json={"product_name": "x", "product_format": "not_a_format", "description": "d"})
check("1-10 rejects unknown format (400)", r3.status_code == 400)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-106-D tests passed.")
