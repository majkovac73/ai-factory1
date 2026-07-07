"""
Image catalog service — step 76.

Maintains a queryable SQLite index (image_assets table) of every generated
image/design asset. Purpose: avoid redundant DALL-E regeneration (real money)
by letting steps 74/75/81 and any future logic find existing assets before
requesting a new generation.

Each record captures:
  - which task generated the asset
  - which variant (listing/delivery) and use_case (listing/delivery/pinterest)
  - which agent produced it
  - where it lives on disk
  - which Etsy listing it's attached to (populated in step 73)
  - which provider/model generated it
"""
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.db.database import SessionLocal
from app.models.image_asset import ImageAsset


class ImageCatalogService:
    """
    Register and query generated image assets in the SQLite catalog.
    All write operations are idempotent on local_path — re-registering
    the same file (e.g. on a retry run) updates the existing record
    rather than inserting a duplicate.
    """

    def register(
        self,
        task_id: str,
        local_path: str,
        variant: str,
        use_case: str,
        agent: str,
        listing_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ImageAsset:
        """
        Register a generated image file in the catalog.

        Args:
            task_id: Task that triggered generation.
            local_path: Absolute path to the saved file.
            variant: 'listing' or 'delivery'.
            use_case: 'listing', 'delivery', or 'pinterest'.
            agent: Name of the agent that generated the image.
            listing_id: Etsy listing ID (if already known/attached).
            provider: Image provider name (e.g. 'dalle3').
            model: Provider model used (e.g. 'dall-e-3').

        Returns:
            The persisted ImageAsset record.
        """
        db = SessionLocal()
        try:
            existing = (
                db.query(ImageAsset)
                .filter(ImageAsset.local_path == str(local_path))
                .first()
            )
            if existing:
                existing.task_id = task_id
                existing.variant = variant
                existing.use_case = use_case
                existing.agent = agent
                if listing_id is not None:
                    existing.listing_id = listing_id
                if provider is not None:
                    existing.provider = provider
                if model is not None:
                    existing.model = model
                db.commit()
                db.refresh(existing)
                return existing

            record = ImageAsset(
                id=str(uuid.uuid4()),
                task_id=task_id,
                local_path=str(local_path),
                variant=variant,
                use_case=use_case,
                agent=agent,
                listing_id=listing_id,
                provider=provider,
                model=model,
                created_at=datetime.utcnow(),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record
        finally:
            db.close()

    def attach_listing(self, local_path: str, listing_id: str) -> bool:
        """
        Update the catalog record for a file to record its Etsy listing ID.
        Called by step 73 after successfully uploading an image to Etsy.

        Returns:
            True if a record was found and updated, False otherwise.
        """
        db = SessionLocal()
        try:
            record = (
                db.query(ImageAsset)
                .filter(ImageAsset.local_path == str(local_path))
                .first()
            )
            if not record:
                return False
            record.listing_id = listing_id
            db.commit()
            return True
        finally:
            db.close()

    def get_by_task(self, task_id: str) -> List[ImageAsset]:
        """Return all catalog entries for a given task."""
        db = SessionLocal()
        try:
            return (
                db.query(ImageAsset)
                .filter(ImageAsset.task_id == task_id)
                .order_by(ImageAsset.created_at)
                .all()
            )
        finally:
            db.close()

    def get_by_listing(self, listing_id: str) -> List[ImageAsset]:
        """Return all catalog entries attached to a given Etsy listing."""
        db = SessionLocal()
        try:
            return (
                db.query(ImageAsset)
                .filter(ImageAsset.listing_id == listing_id)
                .order_by(ImageAsset.created_at)
                .all()
            )
        finally:
            db.close()

    def get_delivery_asset(self, task_id: str) -> Optional[ImageAsset]:
        """
        Return the 'delivery' variant asset for a task, or None if not yet generated.
        Used by step 81 (POD fulfillment) to find the design file without
        regenerating it.
        """
        db = SessionLocal()
        try:
            return (
                db.query(ImageAsset)
                .filter(
                    ImageAsset.task_id == task_id,
                    ImageAsset.variant == "delivery",
                )
                .order_by(ImageAsset.created_at.desc())
                .first()
            )
        finally:
            db.close()

    def get_listing_assets(self, task_id: str) -> List[ImageAsset]:
        """Return all 'listing' variant assets for a task."""
        db = SessionLocal()
        try:
            return (
                db.query(ImageAsset)
                .filter(
                    ImageAsset.task_id == task_id,
                    ImageAsset.variant == "listing",
                )
                .order_by(ImageAsset.created_at)
                .all()
            )
        finally:
            db.close()

    def list_all(self, limit: int = 200) -> List[ImageAsset]:
        """Return the most recent catalog entries, newest first."""
        db = SessionLocal()
        try:
            return (
                db.query(ImageAsset)
                .order_by(ImageAsset.created_at.desc())
                .limit(limit)
                .all()
            )
        finally:
            db.close()
