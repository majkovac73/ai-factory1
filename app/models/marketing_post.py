import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String

from app.models.base import Base


class MarketingPost(Base):
    """
    Records an attempt to publish a task's listing to a marketing
    channel (Pinterest, etc.), so the same listing isn't posted twice
    and so failures/results are auditable.
    """
    __tablename__ = "marketing_posts"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    channel = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending, success, failed
    external_id = Column(String, nullable=True)
    external_url = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)