import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, String

from app.models.base import Base


class Memory(Base):
    __tablename__ = "memory"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    memory_key = Column(String, nullable=False)
    memory_value = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)