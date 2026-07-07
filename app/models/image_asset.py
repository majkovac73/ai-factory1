import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String

from app.models.base import Base


class ImageAsset(Base):
    """
    Queryable index of every generated image/design asset.
    One row per saved file, tracking which task generated it, which variant
    it is, which product/listing it belongs to, and when it was created.

    This catalog is what lets downstream steps (74, 75, 81) and any future
    reuse logic find an existing asset instead of regenerating one — DALL-E 3
    calls cost real money, so avoiding redundant regeneration matters.

    Fields:
      task_id       : Task that triggered generation
      variant       : 'listing' or 'delivery'
      use_case      : 'listing', 'delivery', 'pinterest' — matches validation rules
      agent         : Which agent produced it (ProductImageAgent, SocialImageAgent, etc.)
      local_path    : Absolute filesystem path to the saved file
      listing_id    : Etsy listing ID this asset is attached to (set in step 73)
      provider      : Image provider name (e.g. 'dalle3', 'fake')
      model         : Provider model used (e.g. 'dall-e-3')
    """
    __tablename__ = "image_assets"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String, nullable=False, index=True)
    variant = Column(String, nullable=False)
    use_case = Column(String, nullable=False)
    agent = Column(String, nullable=False)
    local_path = Column(String, nullable=False, unique=True)
    listing_id = Column(String, nullable=True, index=True)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
