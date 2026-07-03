from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class AgentExecution(Base):
    __tablename__ = "agent_executions"

    id = Column(String, primary_key=True, index=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    agent_name = Column(String, nullable=False)
    role = Column(String, nullable=True)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    model_used = Column(String, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="success")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    task = relationship("Task", back_populates="agent_executions")
