from typing import Optional

from fastapi import APIRouter, Query

from app.services.analytics_service import AnalyticsService

router = APIRouter()
analytics_service = AnalyticsService()


@router.get("/events")
def list_events(
    event_type: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    entity_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    events = analytics_service.get_events(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        limit=limit,
    )
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "value": e.value,
            "payload": e.payload,
            "created_at": e.created_at,
        }
        for e in events
    ]


@router.get("/summary")
def analytics_summary():
    return {
        "event_counts": analytics_service.get_event_counts_by_type(),
    }