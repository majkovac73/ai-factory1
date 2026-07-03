import json
from fastapi import APIRouter
from app.db.database import SessionLocal
from app.models.task import Task
from app.core.task_processor import process_task

router = APIRouter()

@router.post("/task")
def create_task(task_type: str, input: str):

    db = SessionLocal()

    task = Task(type=task_type, input=input)
    db.add(task)
    db.commit()
    db.refresh(task)

    # immediately process (simple Phase 1 behavior)
    process_task(task.id)

    return {
        "task_id": task.id,
        "status": task.status
    }


@router.get("/task/{task_id}")
def get_task(task_id: int):

    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()

    output = task.output
    try:
        output = json.loads(output)
    except Exception:
        pass

    return {
        "id": task.id,
        "type": task.type,
        "status": task.status,
        "output": output
    }
