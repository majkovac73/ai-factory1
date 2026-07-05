import logging

from app.db.database import SessionLocal
from app.models.marketing_post import MarketingPost

logger = logging.getLogger("ai-factory")


class MarketingService:
    """
    Coordination layer for publishing a task's listing to one or more
    marketing channels. Channels are registered externally (see
    app/marketing/registry.py, added when Step 60/61 build real
    channels) and passed in here rather than imported directly, so this
    service has no hard dependency on any specific platform.
    """

    def post_to_channel(self, task_id: str, listing: dict, channel) -> dict:
        """
        Args:
            task_id: The task this listing belongs to (for tracking).
            listing: Completed listing dict (e.g. from ListingGeneratorAgent).
            channel: A MarketingChannel instance implementing .post().

        Returns:
            The channel's result dict, also persisted to marketing_posts.
        """
        db = SessionLocal()
        record = MarketingPost(
            task_id=task_id,
            channel=channel.name,
            status="pending",
            payload=listing,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        db.close()

        try:
            result = channel.post(listing)
        except Exception as e:
            logger.error(f"MarketingService: channel '{channel.name}' failed for task {task_id}: {e}")
            result = {"success": False, "external_id": None, "url": None, "error": str(e)}

        db = SessionLocal()
        try:
            record = db.query(MarketingPost).filter(MarketingPost.id == record.id).first()
            record.status = "success" if result.get("success") else "failed"
            record.external_id = result.get("external_id")
            record.external_url = result.get("url")
            record.error_message = result.get("error")
            db.commit()
        finally:
            db.close()

        return result

    def get_posts_for_task(self, task_id: str):
        db = SessionLocal()
        try:
            return db.query(MarketingPost).filter(MarketingPost.task_id == task_id).all()
        finally:
            db.close()