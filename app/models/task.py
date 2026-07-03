from sqlalchemy import Column, Integer, String, Text
from app.db.database import Base

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String)
    status = Column(String, default="pending")
    input = Column(Text)
    output = Column(Text, nullable=True)