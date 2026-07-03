from app.db.database import SessionLocal
from app.models.task import Task


from fastapi import HTTPException

from app.db.database import SessionLocal
from app.models.task import Task
from app.schemas.enums import TaskStatus


class TaskService:
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
