import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.models.base import Base


class EtsyToken(Base):
    """
    Stores the OAuth access/refresh token pair for the shop this app is
    authorized against. Single-row-per-shop design: one shop connection
    per deployment for now (multi-shop support would need a shop_id key).
    """
    __tablename__ = "etsy_tokens"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    shop_id = Column(String, nullable=False)
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)