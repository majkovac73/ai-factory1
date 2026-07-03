from typing import Optional

from pydantic import BaseModel

from app.schemas.enums import TaskStatus


class TaskCreate(BaseModel):
    prompt: str
    metadata: Optional[dict] = None


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class TaskResponse(BaseModel):
    id: int | str
    prompt: str | None = None
    status: str
    result: Optional[str] = None

    class Config:
        from_attributes = True
