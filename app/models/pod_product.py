import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String

from app.models.base import Base


class PODProduct(Base):
    __tablename__ = "pod_products"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String, nullable=False, index=True)
    printify_product_id = Column(String, nullable=True)
    blueprint_id = Column(Integer, nullable=True)
    print_provider_id = Column(Integer, nullable=True)
    variant_ids = Column(JSON, nullable=True)
    etsy_listing_id = Column(String, nullable=True, index=True)
    # P0-4/P0-5: margin auditing + the single deliberate variant actually sold.
    cost_cents = Column(Integer, nullable=True)      # Printify production cost of the sold variant
    price_cents = Column(Integer, nullable=True)     # margin-safe Etsy price we set
    variant_title = Column(String, nullable=True)    # e.g. "Black / L" — stated in the listing
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
