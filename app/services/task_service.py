from fastapi import HTTPException

from app.db.database import SessionLocal
from app.models.task import Task
from app.schemas.enums import TaskStatus, TASK_STATUS_TRANSITIONS
from app.services.task_queue import TaskQueues


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