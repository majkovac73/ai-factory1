"""
Step 104 test — selection diversity (1-7): rotate among top-3 opportunities and
vary the research topic toward in-window occasions.

Usage: python scripts/test_step104_diversity.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()

import app.services.trend_data_service as tds
from app.services.task_service import TaskService


def run_once():
    seen = {}
    # STEP106 1-2: run() now calls _persistent_search over ALL opportunities
    # instead of picking one random index. Capture what it receives.
    with patch.object(tds.TrendDataService, "get_real_trend_signals",
                      return_value={"keywords": [], "rising_queries": {}, "interest_snapshot": {}}), \
         patch.object(agent._research, "research", side_effect=lambda t, s, real_trend_data=None: seen.update(topic=t) or "r"), \
         patch.object(agent._intelligence, "synthesize",
                      return_value={"opportunities": ["OppA", "OppB", "OppC"], "confidence": "low"}), \
         patch.object(agent, "_persistent_search", side_effect=lambda opps, c, st: seen.update(opps=list(opps)) or {"product_name": "x"}), \
         patch.object(agent, "_load_insights_block", return_value=""), \
         patch.object(TaskService, "recent_product_titles", return_value=[]):
        agent.run()
    return seen


# 1-2: persistent search receives ALL opportunities (not a single random index)
s = run_once()
check("1-2 persistent search gets all 3 opportunities", set(s.get("opps") or []) == {"OppA", "OppB", "OppC"})

# topic rotates to an in-window occasion when the dice say so
with patch("app.agents.trend_research_agent.random.random", return_value=0.1), \
     patch("app.core.seasonality.upcoming_occasions", return_value=[{"occasion": "Halloween", "key": "halloween", "keyword": "x", "days_until": 40}]):
    st = run_once()
check("1-7 topic uses the in-window occasion", "Halloween" in (st.get("topic") or ""))

# topic stays the default fixed topic when the dice say no
with patch("app.agents.trend_research_agent.random.random", return_value=0.9), \
     patch("app.core.seasonality.upcoming_occasions", return_value=[{"occasion": "Halloween", "key": "halloween", "keyword": "x", "days_until": 40}]):
    sd = run_once()
check("1-7 topic falls back to fixed research topic", "Halloween" not in (sd.get("topic") or ""))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104 diversity tests passed.")
