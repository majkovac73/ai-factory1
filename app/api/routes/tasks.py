from typing import List

from fastapi import APIRouter, HTTPException

from app.schemas.task import TaskCreate, TaskResponse, TaskStatusUpdate, EtsyListingRequest
from app.services.task_service import TaskService
from app.services.task_processor import TaskProcessor
from app.orchestrator.core import Orchestrator
from app.services.task_queue import TaskQueue

router = APIRouter()
task_service = TaskService()
task_processor = TaskProcessor()
orchestrator = Orchestrator()
task_queue = TaskQueue()


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

@router.get("/queue/status")
def queue_status():
    return {
        "queue_size": task_queue.size(),
        "is_empty": task_queue.is_empty(),
    }

@router.post("/{task_id}/retry", response_model=TaskResponse)
def retry_task(task_id: str):
    return task_service.retry_failed_task(task_id)


@router.post("/retry-failed")
def retry_all_failed_tasks():
    return task_service.retry_all_failed()

@router.post("/etsy/listing", response_model=TaskResponse)
def create_etsy_listing_task(request: EtsyListingRequest):
    task_data = TaskCreate(prompt=request.prompt, type="seo_writing", metadata=request.metadata)
    return task_service.create_task(task_data)