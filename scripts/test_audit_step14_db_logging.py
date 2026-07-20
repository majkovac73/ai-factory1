"""
Audit 2026-07-20 #14 — WARNING/ERROR logs persist to the `logs` table.

The rich agent/worker logs used logging.getLogger('ai-factory') and went to
stdout only, so the DB logs table had ZERO ERROR/CRITICAL rows. install_db_log_handler
forwards WARNING+ into the table without touching call sites.

Usage: python scripts/test_audit_step14_db_logging.py
"""
import os
import sys
import logging
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "logs.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.services.log_service import LogService, install_db_log_handler

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


install_db_log_handler()
install_db_log_handler()  # idempotent
lg = logging.getLogger("ai-factory")
from app.services.log_service import DBLogHandler
check("handler installed exactly once", sum(isinstance(h, DBLogHandler) for h in lg.handlers) == 1)

lg.info("this info should NOT persist via the handler (below WARNING)")
lg.warning("a warning that must persist")
try:
    raise ValueError("boom")
except ValueError:
    lg.error("an error with traceback", exc_info=True)

svc = LogService()
warnings = svc.list_logs(level="WARNING", limit=50)
errors = svc.list_logs(level="ERROR", limit=50)
check("WARNING persisted to logs table", any("must persist" in (w.message or "") for w in warnings))
check("ERROR persisted to logs table", any("with traceback" in (e.message or "") for e in errors))
check("ERROR row carries traceback payload",
      any((e.payload or {}).get("exc") for e in errors))

# INFO from the logger should not have been forwarded by the handler
infos = svc.list_logs(level="INFO", limit=50)
check("INFO not forwarded by handler (level floor is WARNING)",
      not any("should NOT persist" in (i.message or "") for i in infos))

summ = svc.error_summary(24)
check("error_summary counts >=1 error", summ["error_count"] >= 1)
check("error_summary counts >=1 warning", summ["warning_count"] >= 1)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#14 DB-logging tests passed.")
