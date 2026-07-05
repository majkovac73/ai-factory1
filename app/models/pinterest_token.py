import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.models.base import Base


class PinterestToken(Base):
    """
    Stores the OAuth access/refresh token pair for the Pinterest account
    this app is authorized against. Single-row design, same pattern as
    EtsyToken — one Pinterest account connection per deployment for now.
    """
    __tablename__ = "pinterest_tokens"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)