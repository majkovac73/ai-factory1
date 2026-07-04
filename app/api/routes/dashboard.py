from fastapi import APIRouter

from app.schemas.enums import TaskStatus
from app.services.task_service import TaskService
from app.services.task_queue import TaskQueue
from app.services.log_service import LogService

router = APIRouter()
task_service = TaskService()
task_queue = TaskQueue()
log_service = LogService()


@router.get("/overview")
def dashboard_overview():
    all_tasks = task_service.list_tasks()

    status_counts = {status.value: 0 for status in TaskStatus}
    for task in all_tasks:
        if task.status in status_counts:
            status_counts[task.status] += 1

    recent_errors = log_service.list_logs(level="ERROR", limit=10)

    return {
        "total_tasks": len(all_tasks),
        "status_counts": status_counts,
        "queue_size": task_queue.size(),
        "recent_errors": [
            {
                "source": log.source,
                "message": log.message,
                "created_at": log.created_at,
            }
            for log in recent_errors
        ],
    }