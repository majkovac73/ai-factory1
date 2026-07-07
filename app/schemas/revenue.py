from typing import Optional

from pydantic import BaseModel, Field


class SaleCreate(BaseModel):
    task_id: str
    amount: float = Field(..., gt=0, description="Sale amount, must be positive")
    currency: str = "USD"
    quantity: int = Field(default=1, gt=0)
    notes: Optional[str] = None