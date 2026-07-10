"""
Step 103 / A-1 + A-10 test — close the learning loop.

A-10 (views/favorites):
  [1] ListingStatsService.record_stats records a listing_stats event per
      listing that maps to a task (views + 10x favorites), skipping unmapped.
  [2] PerformanceService._engagement_score reads the latest listing_stats.

A-1 (learning loop):
  [3] TrendResearchAgent._load_insights_block summarizes best formats/keywords
      + revenue and is injected into the concept prompt.
  [4] AutonomyService winner-variant cap: allows up to WINNER_VARIANTS_PER_DAY,
      then blocks.

Usage: python scripts/test_step103_learning_loop.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "loop.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.image_asset import ImageAsset
from app.models.analytics_event import AnalyticsEvent  # noqa
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

# map listing 111 -> task dig-1
db = SessionLocal()
db.add(ImageAsset(task_id="dig-1", listing_id="111", variant="delivery",
                  use_case="delivery", agent="X", local_path="/x.png"))
db.commit()
db.close()

# [1] record_stats
from app.services.listing_stats_service import ListingStatsService
lss = ListingStatsService()
n = lss.record_stats([
    {"listing_id": 111, "views": 40, "num_favorers": 3},   # -> dig-1, engagement 70
    {"listing_id": 999, "views": 5, "num_favorers": 0},    # unmapped -> skipped
])
check("1 recorded only mapped listings", n == 1)

from app.services.analytics_service import AnalyticsService
evts = AnalyticsService().get_events(event_type="listing_stats", entity_type="task", entity_id="dig-1", limit=10)
check("1 listing_stats event exists", len(evts) == 1)
check("1 payload has views/favorites", (evts[0].payload or {}).get("views") == 40 and (evts[0].payload or {}).get("favorites") == 3)

# [2] engagement score
from app.services.performance_service import PerformanceService
ps = PerformanceService()
score = ps._engagement_score("dig-1")
# engagement 40 + 30 = 70; ratio 0.70 * 20 = 14
check("2 engagement score from views+10*fav", abs(score - 14.0) < 0.01)
check("2 no stats -> 0", ps._engagement_score("nonexistent") == 0.0)

# [3] insights block
with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()
agent._recent_products = [("A", "single_print"), ("B", "single_print"), ("C", "coloring_page")]
with patch("app.services.best_products_service.BestProductsService.get_best_product_insights",
           return_value={"top_task_types": [("single_print", 3)], "top_keywords": [("boho", 2), ("minimalist", 1)]}), \
     patch("app.services.revenue_service.RevenueService.get_revenue_by_task",
           return_value={"t1": 12.5, "t2": 5.0}):
    block = agent._load_insights_block()
check("3 insights mentions best format", "single_print" in block)
check("3 insights mentions revenue", "17.5" in block or "17.50" in block)
check("3 insights mentions shop mix", "Current shop mix" in block)
agent._insights_block = block
prompt = agent._build_concept_prompt("insight", "")
check("3 insights injected into prompt", "What's working in the shop" in prompt)

# [4] winner-variant cap
from app.services.autonomy_service import AutonomyService
with patch.object(settings, "WINNER_VARIANTS_PER_DAY", 2):
    auto = AutonomyService()
    # reset today's state
    import json
    p = auto._state_path()
    if p.exists():
        p.unlink()
    check("4 can create initially", auto.can_create_winner_variant() is True)
    auto.record_winner_variant()
    auto.record_winner_variant()
    check("4 blocked after cap reached", auto.can_create_winner_variant() is False)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 learning-loop tests passed.")
