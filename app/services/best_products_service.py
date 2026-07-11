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
        """2-1: the LEARNING-LOOP consumer. Rank by REAL REVENUE first; only when
        nothing has sold yet, fall back to engagement VELOCITY (2-2). Reliability
        (pipeline-didn't-crash) never qualifies a product as "best" here — that's
        merchandising noise. Also surfaces the anti-signal: formats piling up
        listings with $0 revenue."""
        from app.core.product_formats import PRODUCT_FORMATS
        from app.services.revenue_service import RevenueService

        revenue_by_task = RevenueService().get_revenue_by_task() or {}
        product_tasks = [t for t in self.task_service.list_tasks() if t.type in PRODUCT_FORMATS]
        by_id = {t.id: t for t in product_tasks}

        earners = sorted(
            [(tid, rev) for tid, rev in revenue_by_task.items() if rev and rev > 0 and tid in by_id],
            key=lambda x: -x[1],
        )
        has_sales = bool(earners)

        if has_sales:
            ranked = [(tid, rev) for tid, rev in earners[:limit]]
            label = "Products that have EARNED real money (bias toward these themes/formats):"
        else:
            scored = [(t.id, self.performance_service.engagement_velocity(t.id)) for t in product_tasks]
            scored = sorted([s for s in scored if s[1] > 0], key=lambda x: -x[1])[:limit]
            ranked = scored
            label = "No sales yet — formats with the most buyer view/favorite VELOCITY (per day):"

        products, type_counts, keyword_counts = [], Counter(), Counter()
        for tid, metric in ranked:
            t = by_id.get(tid)
            if not t:
                continue
            out = t.output_data or {}
            products.append({"task_id": tid, "metric": round(metric, 2), "task_type": t.type,
                             "title": out.get("title"), "keywords": out.get("keywords", [])})
            if t.type:
                type_counts[t.type] += 1
            for kw in out.get("keywords") or []:
                keyword_counts[str(kw).lower().strip()] += 1

        # Anti-signal: formats with several listings and ZERO revenue.
        fmt_total = Counter(t.type for t in product_tasks)
        fmt_earning = Counter(by_id[tid].type for tid, _ in earners if tid in by_id)
        zero_rev_formats = [(fmt, n) for fmt, n in fmt_total.items() if n >= 3 and fmt_earning.get(fmt, 0) == 0]

        return {
            "count": len(products),
            "has_sales": has_sales,
            "label": label,
            "top_task_types": type_counts.most_common(5),
            "top_keywords": keyword_counts.most_common(10),
            "zero_revenue_formats": zero_rev_formats,
            "products": products,
        }