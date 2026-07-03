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

    def get_task(self, task_id: int):
        db = SessionLocal()
        try:
            return db.query(Task).filter(Task.id == task_id).first()
        finally:
            db.close()

    def list_tasks(self):
        db = SessionLocal()
        try:
            return db.query(Task).all()
        finally:
            db.close()
