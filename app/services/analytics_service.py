import logging
import uuid
from datetime import datetime
from typing import Optional

from app.db.database import SessionLocal
from app.models.analytics_event import AnalyticsEvent

logger = logging.getLogger("ai-factory")


class AnalyticsService:
    """
    Central write/read interface for business analytics events.
    Other services (TaskProcessor, MarketingService, and future
    services in Steps 63-65) call record_event() at the moment
    something notable happens, rather than each maintaining their
    own aggregation logic.
    """

    def record_event(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        value: Optional[float] = None,
        payload: Optional[dict] = None,
        transaction_id: Optional[str] = None,
    ) -> None:
        db = SessionLocal()
        try:
            event = AnalyticsEvent(
                id=str(uuid.uuid4()),
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                value=value,
                transaction_id=transaction_id,  # D-3
                payload=payload or {},
                created_at=datetime.utcnow(),
            )
            db.add(event)
            db.commit()
        except Exception as e:
            db.rollback()
            # Analytics failures should never break the calling workflow
            logger.error(f"AnalyticsService failed to record event: {e}")
        finally:
            db.close()

    def get_events(
        self,
        event_type: str = None,
        entity_type: str = None,
        entity_id: str = None,
        limit: int = 100,
    ):
        db = SessionLocal()
        try:
            query = db.query(AnalyticsEvent)
            if event_type:
                query = query.filter(AnalyticsEvent.event_type == event_type)
            if entity_type:
                query = query.filter(AnalyticsEvent.entity_type == entity_type)
            if entity_id:
                query = query.filter(AnalyticsEvent.entity_id == entity_id)
            return query.order_by(AnalyticsEvent.created_at.desc()).limit(limit).all()
        finally:
            db.close()

    def get_event_counts_by_type(self) -> dict:
        """
        Returns a dict of event_type -> count across all recorded events.
        Simple aggregate used by the /analytics/summary route; more
        specific aggregates (revenue totals, per-product performance)
        are added in Steps 63-65 without needing to change this method.
        """
        db = SessionLocal()
        try:
            events = db.query(AnalyticsEvent).all()
        finally:
            db.close()

        counts = {}
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return counts