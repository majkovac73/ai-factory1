import logging

from app.db.database import SessionLocal
from app.models.marketing_post import MarketingPost
from app.services.analytics_service import AnalyticsService

logger = logging.getLogger("ai-factory")


class MarketingService:
    """
    Coordination layer for publishing a task's listing to one or more
    marketing channels. Channels are registered externally (see
    app/marketing/registry.py, added when Step 60/61 build real
    channels) and passed in here rather than imported directly, so this
    service has no hard dependency on any specific platform.
    """

    def __init__(self):
        self.analytics_service = AnalyticsService()

    def post_to_channel(self, task_id: str, listing: dict, channel) -> dict:
        """
        Args:
            task_id: The task this listing belongs to (for tracking).
            listing: Completed listing dict (e.g. from ListingGeneratorAgent).
            channel: A MarketingChannel instance implementing .post().

        Returns:
            The channel's result dict, also persisted to marketing_posts.
        """
        # Capture the primary key as a plain value while the record is still
        # bound to a live session. Accessing record.id after the session is
        # closed triggers a DetachedInstanceError (SQLAlchemy re-loads expired
        # attributes on access) — the real bug that made the Pinterest/refresh
        # marketing step raise "Instance <MarketingPost> is not bound to a
        # Session". Use record_id everywhere after this point.
        db = SessionLocal()
        try:
            record = MarketingPost(
                task_id=task_id,
                channel=channel.name,
                status="pending",
                payload=listing,
            )
            db.add(record)
            db.commit()
            record_id = record.id
        finally:
            db.close()

        try:
            result = channel.post(listing)
        except Exception as e:
            logger.error(f"MarketingService: channel '{channel.name}' failed for task {task_id}: {e}")
            result = {"success": False, "external_id": None, "url": None, "error": str(e)}

        db = SessionLocal()
        try:
            record = db.query(MarketingPost).filter(MarketingPost.id == record_id).first()
            if record:
                record.status = "success" if result.get("success") else "failed"
                record.external_id = result.get("external_id")
                record.external_url = result.get("url")
                record.error_message = result.get("error")
                db.commit()
        finally:
            db.close()

        self.analytics_service.record_event(
            event_type="marketing_post_success" if result.get("success") else "marketing_post_failed",
            entity_type="marketing_post",
            entity_id=record_id,
            payload={"task_id": task_id, "channel": channel.name},
        )

        return result

    def get_posts_for_task(self, task_id: str):
        db = SessionLocal()
        try:
            return db.query(MarketingPost).filter(MarketingPost.task_id == task_id).all()
        finally:
            db.close()