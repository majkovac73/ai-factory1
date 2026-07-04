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

@router.get("/metrics")
def dashboard_metrics():
    all_tasks = task_service.list_tasks()

    done_tasks = [t for t in all_tasks if t.status == TaskStatus.DONE.value]
    failed_tasks = [t for t in all_tasks if t.status == TaskStatus.FAILED.value]
    resolved_count = len(done_tasks) + len(failed_tasks)

    success_rate = (len(done_tasks) / resolved_count) if resolved_count > 0 else None

    retry_counts = [(t.retry_count or 0) for t in all_tasks]
    avg_retry_count = (sum(retry_counts) / len(retry_counts)) if retry_counts else 0

    processing_times = []
    for t in done_tasks:
        if t.created_at and t.updated_at:
            delta = (t.updated_at - t.created_at).total_seconds()
            if delta >= 0:
                processing_times.append(delta)
    avg_processing_seconds = (
        sum(processing_times) / len(processing_times) if processing_times else None
    )

    token_summary = log_service.get_token_usage_summary()

    return {
        "total_tasks": len(all_tasks),
        "done_count": len(done_tasks),
        "failed_count": len(failed_tasks),
        "success_rate": success_rate,
        "average_retry_count": round(avg_retry_count, 2),
        "average_processing_seconds": (
            round(avg_processing_seconds, 2) if avg_processing_seconds is not None else None
        ),
        "token_usage": token_summary,
    }