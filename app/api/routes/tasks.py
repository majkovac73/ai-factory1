from typing import List

from fastapi import APIRouter, HTTPException

from app.schemas.task import TaskCreate, TaskResponse, TaskStatusUpdate
from app.services.task_service import TaskService
from app.services.task_processor import TaskProcessor
from app.orchestrator.core import Orchestrator

router = APIRouter()
task_service = TaskService()
task_processor = TaskProcessor()
orchestrator = Orchestrator()


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

@router.patch("/{task_id}/status", response_model=TaskResponse)
def update_task_status(task_id: str, update: TaskStatusUpdate):
    return task_service.update_status(task_id, update.status.value)

@router.post("/{task_id}/process", response_model=TaskResponse)
def process_task(task_id: str):
    try:
        return task_processor.process(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
@router.post("/run-pending")
def run_pending_tasks():
    return orchestrator.run_pending()