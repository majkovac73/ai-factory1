import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, UniqueConstraint

from app.models.base import Base


class FulfillmentRecord(Base):
    __tablename__ = "fulfillment_records"
    __table_args__ = (
        UniqueConstraint(
            "etsy_receipt_id", "etsy_transaction_id",
            name="uq_receipt_transaction",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    etsy_receipt_id = Column(String, nullable=False, index=True)
    etsy_transaction_id = Column(String, nullable=False, default="")
    task_id = Column(String, nullable=True)
    pod_product_id = Column(String, nullable=True)
    printify_order_id = Column(String, nullable=True)
    status = Column(String, default="submitted", nullable=False)
    tracking_number = Column(String, nullable=True)
    carrier = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
