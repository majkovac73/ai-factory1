"""
Audit 2026-07-20 #15 — per-step pipeline provenance (task_steps + agent_executions).

Both tables were empty despite 147 completed tasks. ExecutionLogService now writes
one TaskStep per pipeline stage (with derived status) + an AgentExecution summary
row (with the task's attributed cost).

Usage: python scripts/test_audit_step15_provenance.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "prov.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.models.task_step import TaskStep
from app.models.agent_execution import AgentExecution
from app.core import cost_context
from app.services.execution_log_service import ExecutionLogService

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


db = SessionLocal()
db.add(Task(id="task-prov", prompt="p", type="coloring_page", status="DONE"))
db.commit()
db.close()

# attribute some cost to the task
with cost_context.cost_attribution("task-prov"):
    cost_context.record_cost(0.04, use_case="image")
    cost_context.record_cost(0.002, use_case="vision_qa")

report = {
    "task_id": "task-prov",
    "stages": {
        "listing_images": {"ok": True, "count": 4},
        "delivery_asset": {"ok": True},
        "pinterest": {"skipped": "Trial-blocked"},
        "attach_publish": {"ok": False, "error": "digital file readback failed"},
    },
    "blocked": True,
}

summary = ExecutionLogService().record_pipeline_run("task-prov", report)
check("4 steps written", summary["steps_written"] == 4)
check("task cost attributed to summary (~0.042)", abs(summary["cost_usd"] - 0.042) < 1e-6)

db = SessionLocal()
steps = db.query(TaskStep).filter(TaskStep.task_id == "task-prov").all()
execs = db.query(AgentExecution).filter(AgentExecution.task_id == "task-prov").all()
by_name = {s.step_name: s for s in steps}
db.close()

check("task_steps populated (was empty)", len(steps) == 4)
check("skipped stage -> status 'skipped'", by_name["pinterest"].status == "skipped")
check("failed stage -> status 'failed'", by_name["attach_publish"].status == "failed")
check("failed stage carries error text", "readback" in (by_name["attach_publish"].error or ""))
check("ok stage -> status 'success'", by_name["listing_images"].status == "success")
check("agent_executions summary row written (was empty)", len(execs) == 1)
check("summary marked blocked", execs[0].status == "blocked")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#15 provenance tests passed.")
