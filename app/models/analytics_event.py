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
    event_type = Column(String, nullable=False, index=True)  # D-3: indexed for fast filtering
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False, index=True)  # D-3: indexed (per-task lookups)
    value = Column(Float, nullable=True)  # optional numeric value (e.g. revenue amount)
    # D-3: promote the sale transaction_id out of the JSON payload into a real
    # indexed column so idempotency checks are O(1) instead of scanning 10k rows.
    transaction_id = Column(String, nullable=True, index=True)
    payload = Column(JSON, nullable=True)  # arbitrary structured details about the event
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)