from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import TaskStatus


class TaskCreate(BaseModel):
    prompt: str
    type: Optional[str] = None
    metadata: Optional[dict] = None

class EtsyListingRequest(BaseModel):
    prompt: str
    metadata: Optional[dict] = None

class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class TaskResponse(BaseModel):
    id: int | str
    prompt: str | None = None
    type: Optional[str] = None
    status: str
    result: Optional[str] = None
    metadata: Optional[dict] = Field(default=None, validation_alias="metadata_")
    output_data: Optional[dict] = None
    error_message: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)