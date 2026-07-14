"""
Seasonal/evergreen balance — the factory keeps a year-round evergreen base
instead of building 100% of one in-window occasion.

Usage: python scripts/test_seasonal_evergreen_balance.py
"""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "sev.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── seasonal_prompt_block modes ──
from app.core.seasonality import seasonal_prompt_block
# mid-July: back_to_school is the in-window occasion
jul = date(2026, 7, 14)
seasonal = seasonal_prompt_block(jul, mode="seasonal")
evergreen = seasonal_prompt_block(jul, mode="evergreen")
check("seasonal block steers toward the occasion", "shopping NOW" in seasonal and "PREFER" in seasonal)
check("evergreen block demands a year-round product", "EVERGREEN" in evergreen and "year-round" in evergreen.lower())
check("evergreen block says NOT tied to an occasion", "NOT tied to any" in evergreen or "not tied to any" in evergreen.lower())
check("evergreen block names the in-window occasion to AVOID", "back to school" in evergreen.lower() or "avoid" in evergreen.lower())

# ── back-to-school opens a little later now (min_w 3, max_w 8) ──
from app.core.seasonality import occasion_in_window
# 8 weeks before Sep 1 ~= Jul 7; 9 weeks ~= Jun 30 (should now be OUT of window)
check("back_to_school out of window in late June (was in window before)",
      occasion_in_window("back_to_school", date(2026, 6, 28)) is False)
check("back_to_school in window mid-July", occasion_in_window("back_to_school", date(2026, 7, 20)) is True)

# ── run() decides a mode by SEASONAL_PRODUCT_RATIO ──
with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()

import app.services.trend_data_service as tds
from app.services.task_service import TaskService


def run_mode(rand_val, ratio):
    seen = {}
    with patch.object(settings, "SEASONAL_PRODUCT_RATIO", ratio), \
         patch("app.agents.trend_research_agent.random.random", return_value=rand_val), \
         patch("app.core.seasonality.upcoming_occasions", return_value=[{"occasion": "Back to school", "key": "back_to_school", "keyword": "x", "days_until": 45}]), \
         patch.object(tds.TrendDataService, "get_real_trend_signals", return_value={"rising_queries": {}, "interest_trend": {}}), \
         patch.object(agent._research, "research", side_effect=lambda t, s, real_trend_data=None: seen.update(topic=t) or "r"), \
         patch.object(agent._intelligence, "synthesize", return_value={"opportunities": [], "confidence": "low"}), \
         patch.object(agent, "_load_insights_block", return_value=""), \
         patch.object(TaskService, "recent_product_titles", return_value=[]):
        agent.run()
    return agent._seasonal_mode, seen.get("topic", "")


# random 0.1 < ratio 0.30 -> seasonal; random 0.5 > 0.30 -> evergreen
mode_s, topic_s = run_mode(0.1, 0.30)
check("low dice -> seasonal mode", mode_s is True)
check("seasonal topic mentions the occasion", "back to school" in topic_s.lower())
mode_e, topic_e = run_mode(0.5, 0.30)
check("high dice -> evergreen mode", mode_e is False)
check("evergreen topic is the generic digital topic", "back to school" not in topic_e.lower())

# ratio 0 -> never seasonal
mode_never, _ = run_mode(0.0, 0.0)
check("ratio 0 -> always evergreen", mode_never is False)

# ── evergreen enforcement: occasion concept rejected in evergreen mode ──
agent._seasonal_mode = False
agent._recent_products = []
state = agent._new_search_state()
import json as _json
occasion_concept = {"product_name": "Back to School Classroom Planner", "product_format": "pdf_planner_or_guide",
                    "description": "A back to school classroom planner for teachers.", "page_count": 5}
evergreen_concept = {"product_name": "Minimalist Mountain Wall Art", "product_format": "single_print",
                     "description": "A calm minimalist mountain wall art print."}
gen_seq = iter([occasion_concept, evergreen_concept])
agent._generate = lambda prompt: _json.dumps(next(gen_seq))
agent._build_concept_prompt = lambda insight, feedback: "p"
agent._validate_product = lambda d: None
agent._dedup_error = lambda d: None
agent._attach_market = lambda d: None


class FakeScore:
    def score(self, data, trend_data=None, recent_titles=None):
        return {"total": 95, "passed": True, "min_score": 90, "floors": {}, "judges": {}, "retry_feedback": "x"}


agent._score_service = FakeScore()
with patch.object(settings, "PRODUCT_SCORE_ENFORCE", True):
    winner = agent._propose_from_insight("insight", "low", state)
check("evergreen mode: occasion concept rejected (raw), evergreen one wins",
      winner is not None and winner["product_name"] == "Minimalist Mountain Wall Art")
check("evergreen mode: the occasion concept consumed a RAW retry (not scored)", state["raw"] >= 1)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All seasonal/evergreen balance tests passed.")
