import logging
from collections import Counter

from app.services.performance_service import PerformanceService
from app.services.task_service import TaskService

logger = logging.getLogger("ai-factory")


class BestProductsService:
    """
    Surfaces the top-performing products from PerformanceService's
    scores (Step 64) and extracts common attributes among them —
    task_type and keyword overlap — so the shop owner can see not just
    which products are winning, but what they have in common. Reads
    on demand from PerformanceService/TaskService; no new database
    table or stored state is introduced.
    """

    MIN_SCORE_FOR_BEST = 40.0  # a task needs at least this composite score to count as "best"

    def __init__(self):
        self.performance_service = PerformanceService()
        self.task_service = TaskService()

    def get_best_products(self, limit: int = 10, min_score: float = None) -> list:
        threshold = self.MIN_SCORE_FOR_BEST if min_score is None else min_score

        all_scores = self.performance_service.score_all_tasks()
        qualifying = [s for s in all_scores if s["score"] >= threshold]

        results = []
        for entry in qualifying[:limit]:
            task = self.task_service.get_task(entry["task_id"])
            if not task:
                continue

            output = task.output_data or {}
            results.append({
                "task_id": entry["task_id"],
                "score": entry["score"],
                "breakdown": entry["breakdown"],
                "task_type": task.type,
                "title": output.get("title"),
                "keywords": output.get("keywords", []),
            })

        return results

    def get_best_product_insights(self, limit: int = 10, min_score: float = None) -> dict:
        best = self.get_best_products(limit=limit, min_score=min_score)

        if not best:
            return {
                "count": 0,
                "average_score": 0,
                "top_task_types": [],
                "top_keywords": [],
                "products": [],
            }

        average_score = round(sum(p["score"] for p in best) / len(best), 2)

        task_type_counts = Counter(p["task_type"] for p in best if p["task_type"])
        keyword_counts = Counter()
        for p in best:
            for kw in p.get("keywords") or []:
                keyword_counts[kw.lower().strip()] += 1

        return {
            "count": len(best),
            "average_score": average_score,
            "top_task_types": task_type_counts.most_common(5),
            "top_keywords": keyword_counts.most_common(10),
            "products": best,
        }