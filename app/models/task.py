import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base
from app.schemas.enums import TaskStatus


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=True)
    prompt = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=True)
    input_data = Column(JSON, nullable=False, default={})
    output_data = Column(JSON, nullable=True)
    result = Column(Text, nullable=True)
    status = Column(String, default=TaskStatus.NEW.value, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    type = Column(String, nullable=True)
    input = Column(Text, nullable=True)
    output = Column(Text, nullable=True)
    current_step = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    tags = Column(JSON, nullable=True)

    task_steps = relationship("TaskStep", back_populates="task", cascade="all, delete-orphan")
    agent_executions = relationship("AgentExecution", back_populates="task", cascade="all, delete-orphan")