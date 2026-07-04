from typing import List, Optional

from fastapi import APIRouter, Query

from app.services.log_service import LogService

router = APIRouter()
log_service = LogService()


@router.get("")
@router.get("/")
def list_logs(
    source: Optional[str] = Query(default=None, description="Filter by log source, e.g. 'TaskProcessor'"),
    level: Optional[str] = Query(default=None, description="Filter by level: INFO, WARNING, ERROR, DEBUG"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max number of logs to return"),
):
    logs = log_service.list_logs(source=source, level=level, limit=limit)
    return [
        {
            "id": log.id,
            "level": log.level,
            "source": log.source,
            "message": log.message,
            "payload": log.payload,
            "created_at": log.created_at,
        }
        for log in logs
    ]