import logging

from app.services.task_service import TaskService
from app.services.marketing_service import MarketingService
from app.marketing.pinterest_channel import PinterestChannel

logger = logging.getLogger("ai-factory")


class SEOPostingService:
    """
    Bridges a task's QA-validated SEO output (Task.output_data, produced
    by the core pipeline in Steps 33-36) with the marketing channel
    abstraction (Step 59) and the Pinterest channel (Step 60).

    Responsibility: given a task_id, build a channel-ready listing dict
    from that task's validated SEO copy, then post it through
    MarketingService — without requiring the full Etsy listing
    (price/shipping/etc.) that ListingGeneratorAgent produces, since a
    marketing post only needs title/description/keywords.

    Idempotency: refuses to post the same task to the same channel
    twice if a prior post to that channel already succeeded, so retries
    or duplicate API calls don't spam the channel with repeat posts.
    """

    SUPPORTED_CHANNELS = {"pinterest"}

    def __init__(self):
        self.task_service = TaskService()
        self.marketing_service = MarketingService()

    def post_task_seo(self, task_id: str, channel_name: str = "pinterest") -> dict:
        if channel_name not in self.SUPPORTED_CHANNELS:
            raise ValueError(
                f"Unknown marketing channel '{channel_name}'. "
                f"Supported channels: {sorted(self.SUPPORTED_CHANNELS)}"
            )

        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if not task.output_data:
            raise ValueError(
                f"Task {task_id} has no validated SEO output yet "
                f"(task must reach DONE status before it can be posted)"
            )

        existing_posts = self.marketing_service.get_posts_for_task(task_id)
        already_posted = any(
            p.channel == channel_name and p.status == "success"
            for p in existing_posts
        )
        if already_posted:
            raise ValueError(
                f"Task {task_id} was already successfully posted to '{channel_name}'. "
                f"Refusing to post again."
            )

        listing = {
            "title": task.output_data.get("title", ""),
            "description": task.output_data.get("description", ""),
            "keywords": task.output_data.get("keywords", []),
        }

        channel = self._build_channel(channel_name)

        result = self.marketing_service.post_to_channel(
            task_id=task_id,
            listing=listing,
            channel=channel,
        )

        logger.info(
            f"SEOPostingService: task {task_id} -> {channel_name} "
            f"(success={result.get('success')})"
        )

        return result

    def _build_channel(self, channel_name: str):
        if channel_name == "pinterest":
            return PinterestChannel()
        raise ValueError(f"No channel implementation registered for '{channel_name}'")