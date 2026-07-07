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
    MARKETING_WEIGHT = 20

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

    def _marketing_score(self, task_id: str) -> float:
        success_events = self.analytics_service.get_events(
            event_type="marketing_post_success",
            entity_type="marketing_post",
            limit=1000,
        )
        failed_events = self.analytics_service.get_events(
            event_type="marketing_post_failed",
            entity_type="marketing_post",
            limit=1000,
        )

        success_count = sum(
            1 for e in success_events if (e.payload or {}).get("task_id") == task_id
        )
        failed_count = sum(
            1 for e in failed_events if (e.payload or {}).get("task_id") == task_id
        )

        if success_count == 0 and failed_count == 0:
            return 0.0

        ratio = success_count / (success_count + failed_count)
        return round(ratio * self.MARKETING_WEIGHT, 2)

    def score_task(self, task_id: str) -> dict:
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        revenue_points = self._revenue_score(task_id)
        reliability_points = self._reliability_score(task)
        marketing_points = self._marketing_score(task_id)

        total = round(revenue_points + reliability_points + marketing_points, 2)

        return {
            "task_id": task_id,
            "status": task.status,
            "score": total,
            "breakdown": {
                "revenue_points": revenue_points,
                "reliability_points": reliability_points,
                "marketing_points": marketing_points,
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