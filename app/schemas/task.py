from typing import Optional

from pydantic import BaseModel


class TaskCreate(BaseModel):
    prompt: str
    metadata: Optional[dict] = None


class TaskResponse(BaseModel):
    id: int | str
    prompt: str | None = None
    status: str
    result: Optional[str] = None

    class Config:
        from_attributes = True
