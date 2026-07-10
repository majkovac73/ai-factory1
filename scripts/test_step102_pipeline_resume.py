"""
Step 102 / P0-9 test — pipeline crash-resumability.

  [1] mark_pipeline_completed stamps output_data.pipeline_status=COMPLETED + listing_id.
  [2] enqueue_new_tasks re-enqueues every NEW task (stranded by the in-memory queue).
  [3] get_resumable_pipeline_tasks returns DONE tasks with NO pipeline_status
      within the window, and EXCLUDES: COMPLETED, BLOCKED, and too-old tasks;
      respects the limit.

Usage: python scripts/test_step102_pipeline_resume.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "resume.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.services.task_service import TaskService
from app.services.task_queue import TaskQueue

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)
ts = TaskService()


def add_task(tid, status, output=None, updated=None, ttype="single_print"):
    db = SessionLocal()
    try:
        t = Task(id=tid, prompt="p", type=ttype, status=status,
                 input_data={}, output_data=output)
        db.add(t)
        db.commit()
        if updated is not None:
            t2 = db.query(Task).filter(Task.id == tid).first()
            t2.updated_at = updated
            db.commit()
    finally:
        db.close()


# [1] mark completed
add_task("t-done", "DONE", output={"title": "X"})
ts.mark_pipeline_completed("t-done", "listing-123")
db = SessionLocal()
row = db.query(Task).filter(Task.id == "t-done").first()
od = row.output_data or {}
db.close()
check("1 pipeline_status COMPLETED stamped", od.get("pipeline_status") == "COMPLETED")
check("1 listing_id stamped", od.get("listing_id") == "listing-123")

# [2] enqueue NEW
# clear the singleton queue
q = TaskQueue()
while not q.is_empty():
    q.dequeue(block=False)
add_task("t-new1", "NEW")
add_task("t-new2", "NEW")
n = ts.enqueue_new_tasks()
check("2 enqueue_new_tasks returns count of NEW", n == 2)
check("2 queue now has 2", q.size() == 2)

# [3] resumable scan
now = datetime.utcnow()
add_task("r-crashed", "DONE", output={"title": "crashed"}, updated=now)              # resumable
add_task("r-completed", "DONE", output={"pipeline_status": "COMPLETED"}, updated=now)  # excluded
add_task("r-blocked", "DONE", output={"pipeline_status": "BLOCKED_NO_PRODUCT"}, updated=now)  # excluded
add_task("r-old", "DONE", output={"title": "old"}, updated=now - timedelta(hours=48))  # excluded (old)

resumable = ts.get_resumable_pipeline_tasks(window_hours=6, limit=5)
ids = {tid for tid, _ in resumable}
check("3 crashed-mid-pipeline task IS resumable", "r-crashed" in ids)
check("3 COMPLETED task excluded", "r-completed" not in ids)
check("3 BLOCKED task excluded", "r-blocked" not in ids)
check("3 too-old task excluded", "r-old" not in ids)
# t-done was marked COMPLETED above -> excluded too
check("3 already-completed t-done excluded", "t-done" not in ids)

# limit respected
add_task("r-c2", "DONE", output={"title": "c2"}, updated=now)
add_task("r-c3", "DONE", output={"title": "c3"}, updated=now)
limited = ts.get_resumable_pipeline_tasks(window_hours=6, limit=2)
check("3 limit respected", len(limited) == 2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 pipeline-resume tests passed.")
