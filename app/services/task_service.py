from app.db.database import SessionLocal
from app.models.task import Task


class TaskService:
    def create_task(self, task_data):
        db = SessionLocal()
        try:
            task = Task(
                prompt=task_data.prompt,
                metadata_=task_data.metadata,
                status="NEW",
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            return task
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
