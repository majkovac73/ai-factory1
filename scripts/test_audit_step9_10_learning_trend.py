"""
Audit 2026-07-20 #9 + #10 — blind learning loop + trend-signal visibility.

#9: below LEARNING_MIN_VIEWS_FOR_SIGNAL total views with $0 revenue, the insights
    block must SUPPRESS internal "best format" bias and STEER toward external
    Google-Trends rising queries. With real traffic/sales, internal bias returns.
#10: each cycle records a `trend_signal` analytics event with coverage counts.

Usage: python scripts/test_audit_step9_10_learning_trend.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "lt.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from app.db.database import Base, engine
from app.services.analytics_service import AnalyticsService
from app.agents.trend_research_agent import TrendResearchAgent
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def insights_block(total_views, total_rev, rising):
    agent = TrendResearchAgent.__new__(TrendResearchAgent)
    agent._recent_products = []
    agent._trend_data = {"rising_queries": {"planner": rising}} if rising else {"rising_queries": {}}
    bps = MagicMock()
    bps.return_value.get_best_product_insights.return_value = {
        "label": "No sales yet — formats with the most buyer view/favorite VELOCITY (per day):",
        "top_task_types": [("coloring_page", 5), ("pdf_planner_or_guide", 3)],
        "top_keywords": [("cats", 4)],
        "zero_revenue_formats": [],
        "total_views": total_views,
    }
    rs = MagicMock()
    rs.return_value.get_revenue_by_task.return_value = {"t1": total_rev}
    rs.return_value.profit_by_format.return_value = {}
    with patch("app.services.best_products_service.BestProductsService", bps), \
         patch("app.services.revenue_service.RevenueService", rs), \
         patch.object(settings, "LEARNING_MIN_VIEWS_FOR_SIGNAL", 50):
        return agent._load_insights_block()


# #9 — sparse internal signal -> external steer, no internal bias
blk = insights_block(total_views=7, total_rev=0, rising=["cat planner", "budget planner"])
check("9 sparse: no internal 'VELOCITY' bias line", "VELOCITY" not in blk)
check("9 sparse: steers to external demand", "external" in blk.lower() or "rising" in blk.lower())
check("9 sparse: names real rising queries", "cat planner" in blk)

# #9 — real traffic -> internal bias returns
blk2 = insights_block(total_views=500, total_rev=0, rising=["x"])
check("9 traffic: internal velocity bias present", "VELOCITY" in blk2 or "coloring_page" in blk2)

# #9 — a sale -> internal bias returns even with low views
blk3 = insights_block(total_views=2, total_rev=12.0, rising=["x"])
check("9 sale: internal bias present despite low views", "coloring_page" in blk3 or "EARNED" in blk3)

# #10 — trend_signal event recorded
TrendResearchAgent._record_trend_signal({
    "keywords": ["planner", "coloring", "wall art"],
    "rising_queries": {"planner": ["cat planner", "budget"], "coloring": ["dog coloring"], "wall art": []},
})
evs = AnalyticsService().get_events(event_type="trend_signal", limit=5)
check("10 trend_signal event recorded", len(evs) == 1)
p = evs[0].payload if evs else {}
check("10 keywords_fetched=3", p.get("keywords_fetched") == 3)
check("10 rising_query_count=3", p.get("rising_query_count") == 3)
check("10 matched=2 (keywords with >=1 rising query)", p.get("matched") == 2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#9/#10 tests passed.")
