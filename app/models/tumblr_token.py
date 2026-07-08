import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.models.base import Base


class TumblrToken(Base):
    """
    Stores the OAuth 2.0 access/refresh token pair for the Tumblr account
    this app is authorized against. Single-row design, same pattern as
    EtsyToken/PinterestToken — one Tumblr account connection per deployment.

    Tumblr OAuth 2.0 access tokens expire (expires_in seconds); the
    `offline_access` scope grants a refresh_token used to mint new ones.
    """
    __tablename__ = "tumblr_tokens"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
