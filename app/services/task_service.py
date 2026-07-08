from fastapi import HTTPException

from app.db.database import SessionLocal
from app.models.task import Task
from app.schemas.enums import TaskStatus, TASK_STATUS_TRANSITIONS
from app.services.task_queue import TaskQueue   


class TaskService:
    def __init__(self):
        self.queue = TaskQueue()

    def create_task(self, task_data):
        if not task_data.prompt or not task_data.prompt.strip():
            raise HTTPException(status_code=422, detail="prompt cannot be empty")

        db = SessionLocal()
        try:
            task = Task(
                prompt=task_data.prompt,
                type=task_data.type or "general",
                metadata_=task_data.metadata,
                status=TaskStatus.NEW.value,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            self.queue.enqueue(task.id)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to create task: {e}")
        finally:
            db.close()

    def get_task(self, task_id: str):
        db = SessionLocal()
        try:
            return db.query(Task).filter(Task.id == task_id).first()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch task: {e}")
        finally:
            db.close()

    def list_tasks(self):
        db = SessionLocal()
        try:
            return db.query(Task).all()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list tasks: {e}")
        finally:
            db.close()

    def update_status(self, task_id: str, new_status: str):
        valid_values = {s.value for s in TaskStatus}
        if new_status not in valid_values:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{new_status}'. Must be one of: {sorted(valid_values)}",
            )

        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            current_status = task.status
            allowed_next = TASK_STATUS_TRANSITIONS.get(current_status, set())

            if new_status == current_status:
                raise HTTPException(
                    status_code=422,
                    detail=f"Task is already in status '{current_status}'",
                )

            if new_status not in allowed_next:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Illegal transition: cannot move task from "
                        f"'{current_status}' to '{new_status}'. "
                        f"Allowed next states: {sorted(allowed_next) if allowed_next else 'none (terminal state)'}"
                    ),
                )

            task.status = new_status
            db.commit()
            db.refresh(task)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to update task status: {e}")
        finally:
            db.close()

    def save_plan(self, task_id: str, plan: dict):
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            task.metadata_ = plan
            db.commit()
            db.refresh(task)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to save task plan: {e}")
        finally:
            db.close()

    def save_result(self, task_id: str, result: str):
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            task.result = result
            db.commit()
            db.refresh(task)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to save task result: {e}")
        finally:
            db.close()

    def save_qa_result(self, task_id: str, output_data: dict = None, error_message: str = None):
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            task.output_data = output_data
            task.error_message = error_message
            db.commit()
            db.refresh(task)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to save QA result: {e}")
        finally:
            db.close()

    def record_pipeline_block(self, task_id: str, reason: str):
        """
        Record that the post-completion pipeline refused to create a listing
        for this task because no verified real product was behind it (step 90
        hard gate). Does not change task.status — the task's own QA/execution
        already completed successfully; it's the downstream Etsy listing that
        was blocked. Surfaced via output_data so the dashboard can show it.
        """
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            merged = dict(task.output_data or {})
            merged["pipeline_status"] = "BLOCKED_NO_PRODUCT"
            merged["pipeline_blocked_reason"] = reason
            task.output_data = merged
            db.commit()
            db.refresh(task)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to record pipeline block: {e}")
        finally:
            db.close()

    MAX_TASK_RETRIES = 5

    def retry_failed_task(self, task_id: str):
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            if task.status != TaskStatus.FAILED.value:
                raise HTTPException(
                    status_code=422,
                    detail=f"Only FAILED tasks can be retried (current status: '{task.status}')",
                )

            if (task.retry_count or 0) >= self.MAX_TASK_RETRIES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Task {task_id} has exceeded max retries ({self.MAX_TASK_RETRIES}), not requeuing",
                )

            task.status = TaskStatus.NEW.value
            task.error_message = None
            db.commit()
            db.refresh(task)

            self.queue.enqueue(task.id)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to retry task: {e}")
        finally:
            db.close()

    def retry_all_failed(self):
        db = SessionLocal()
        try:
            failed_tasks = (
                db.query(Task)
                .filter(Task.status == TaskStatus.FAILED.value)
                .filter((Task.retry_count == None) | (Task.retry_count < self.MAX_TASK_RETRIES))
                .all()
            )
            task_ids = [t.id for t in failed_tasks]
        finally:
            db.close()

        results = {"requeued": [], "skipped": []}
        for task_id in task_ids:
            try:
                self.retry_failed_task(task_id)
                results["requeued"].append(task_id)
            except HTTPException as e:
                results["skipped"].append({"task_id": task_id, "reason": e.detail})
        return results
    
    def recover_orphaned_tasks(self):
        """
        Finds tasks stuck in a non-terminal, non-NEW state (PLANNED,
        RUNNING, QA) — these can only be leftovers from a crash or
        unclean shutdown, since normal processing always resolves to
        DONE or FAILED. Marks them FAILED (we cannot trust their
        in-flight state) then attempts to retry them if under the cap.
        """
        orphan_statuses = {TaskStatus.PLANNED.value, TaskStatus.RUNNING.value, TaskStatus.QA.value}

        db = SessionLocal()
        try:
            orphans = db.query(Task).filter(Task.status.in_(orphan_statuses)).all()
            orphan_ids = [t.id for t in orphans]
        finally:
            db.close()

        results = {"recovered": [], "failed_permanently": []}

        for task_id in orphan_ids:
            db = SessionLocal()
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if not task:
                    continue
                task.status = TaskStatus.FAILED.value
                task.error_message = "Recovered after server restart: task was orphaned mid-processing"
                db.commit()
            except Exception:
                db.rollback()
                continue
            finally:
                db.close()

            try:
                self.retry_failed_task(task_id)
                results["recovered"].append(task_id)
            except HTTPException as e:
                results["failed_permanently"].append({"task_id": task_id, "reason": e.detail})

        return results

    def increment_retry_count(self, task_id: str):
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            task.retry_count = (task.retry_count or 0) + 1
            db.commit()
            db.refresh(task)
            return task
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to increment retry count: {e}")
        finally:
            db.close()