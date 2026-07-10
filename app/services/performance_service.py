import logging

from app.services.task_service import TaskService
from app.services.revenue_service import RevenueService
from app.services.analytics_service import AnalyticsService

logger = logging.getLogger("ai-factory")


class PerformanceService:
    """
    Computes a composite performance score (0-100) for each task/product,
    combining revenue generated, pipeline reliability (retries needed to
    reach DONE), and marketing channel success. Built on top of Step 62
    (AnalyticsService) and Step 63 (RevenueService) — no new database
    table is introduced; scores are computed on demand from existing
    Task, AnalyticsEvent, and MarketingPost data so they always reflect
    the latest state without needing a separate sync/cache step.
    """

    MAX_RETRIES_CONSIDERED = 5  # matches TaskService.MAX_TASK_RETRIES
    REVENUE_CAP_FOR_FULL_SCORE = 100.0  # revenue at/above this earns full revenue points

    REVENUE_WEIGHT = 50
    RELIABILITY_WEIGHT = 30
    # A-10: engagement (views + 10x favorites) replaces the old marketing-post
    # score — before the first sale, "did the pipeline post" told us nothing
    # about whether buyers actually respond. Views/favorites are the earliest
    # real demand signal.
    ENGAGEMENT_WEIGHT = 20
    ENGAGEMENT_CAP_FOR_FULL_SCORE = 100.0  # views + 10*favorites at/above this = full points

    def __init__(self):
        self.task_service = TaskService()
        self.revenue_service = RevenueService()
        self.analytics_service = AnalyticsService()

    def _revenue_score(self, task_id: str) -> float:
        summary = self.revenue_service.get_total_revenue(task_id=task_id)
        revenue = summary.get("total_revenue", 0) or 0
        ratio = min(revenue / self.REVENUE_CAP_FOR_FULL_SCORE, 1.0)
        return round(ratio * self.REVENUE_WEIGHT, 2)

    def _reliability_score(self, task) -> float:
        retry_count = task.retry_count or 0
        ratio = max(0.0, 1 - (retry_count / self.MAX_RETRIES_CONSIDERED))
        return round(ratio * self.RELIABILITY_WEIGHT, 2)

    def _engagement_score(self, task_id: str) -> float:
        """A-10: score from the latest listing_stats event (views + 10x favorites)."""
        events = self.analytics_service.get_events(
            event_type="listing_stats",
            entity_type="task",
            entity_id=task_id,
            limit=50,
        )
        if not events:
            return 0.0
        latest = events[0]  # get_events returns newest first
        payload = latest.payload or {}
        views = payload.get("views", 0) or 0
        favorites = payload.get("favorites", 0) or 0
        engagement = views + 10 * favorites
        ratio = min(engagement / self.ENGAGEMENT_CAP_FOR_FULL_SCORE, 1.0)
        return round(ratio * self.ENGAGEMENT_WEIGHT, 2)

    def score_task(self, task_id: str) -> dict:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        revenue_points = self._revenue_score(task_id)
        reliability_points = self._reliability_score(task)
        engagement_points = self._engagement_score(task_id)

        total = round(revenue_points + reliability_points + engagement_points, 2)

        return {
            "task_id": task_id,
            "status": task.status,
            "score": total,
            "breakdown": {
                "revenue_points": revenue_points,
                "reliability_points": reliability_points,
                "engagement_points": engagement_points,
            },
        }

    def score_all_tasks(self) -> list:
        all_tasks = self.task_service.list_tasks()
        scores = []
        for task in all_tasks:
            try:
                scores.append(self.score_task(task.id))
            except Exception as e:
                logger.error(f"PerformanceService: failed to score task {task.id}: {e}")

        scores.sort(key=lambda s: s["score"], reverse=True)
        return scores