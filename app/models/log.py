import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, String

from app.models.base import Base


class Log(Base):
    __tablename__ = "logs"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    level = Column(String, nullable=False)
    source = Column(String, nullable=False)
    message = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)