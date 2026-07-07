import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, String

from app.models.base import Base


class AnalyticsEvent(Base):
    """
    Generic, append-only event log for business-level analytics —
    distinct from app/models/log.py, which is for debug/error/system
    logs. Each row is one thing that happened (a task completing, a
    marketing post succeeding, a sale being recorded in a future step),
    tagged with an event_type so later steps (63-65: revenue tracking,
    performance scoring, best-product detection) can query and
    aggregate without needing new tables for every new metric.
    """
    __tablename__ = "analytics_events"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String, nullable=False)  # e.g. "task_completed", "marketing_post_success"
    entity_type = Column(String, nullable=False)  # e.g. "task", "marketing_post"
    entity_id = Column(String, nullable=False)
    value = Column(Float, nullable=True)  # optional numeric value (e.g. future revenue amount)
    payload = Column(JSON, nullable=True)  # arbitrary structured details about the event
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)