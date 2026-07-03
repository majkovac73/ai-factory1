from typing import List

from fastapi import APIRouter, HTTPException

from app.schemas.task import TaskCreate, TaskResponse
from app.services.task_service import TaskService

router = APIRouter()
task_service = TaskService()


@router.post("", response_model=TaskResponse)
@router.post("/", response_model=TaskResponse)
def create_task(task: TaskCreate):
    return task_service.create_task(task)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("", response_model=List[TaskResponse])
@router.get("/", response_model=List[TaskResponse])
def list_tasks():
    return task_service.list_tasks()