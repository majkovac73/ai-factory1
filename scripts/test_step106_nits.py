"""
Step 106-G test — 5-1 log truncation, 5-2 judge min_score, 5-4 load order,
5-8 rule_version.

Usage: python scripts/test_step106_nits.py
"""
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s106g.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 5-1: BaseAgent._generate truncates logged prompt/output ──
from app.agents.base_agent import BaseAgent


class FakeLLM:
    last_usage = None

    async def generate(self, model, prompt, **kwargs):
        return "Z" * 5000


logged = {}


class FakeLog:
    def info(self, source, message, payload):
        logged["payload"] = payload

    def error(self, source, message, payload):
        logged["payload"] = payload


a = BaseAgent(provider=FakeLLM(), model="openai/gpt-4o-mini")
a.log_service = FakeLog()
with patch.object(settings, "LLM_LOG_MAX_CHARS", 2000):
    a._generate("X" * 5000)
check("5-1 logged prompt truncated to 2000", len(logged["payload"]["prompt"]) == 2000)
check("5-1 logged output truncated to 2000", len(logged["payload"]["output"]) == 2000)

# ── 5-2: judge gets an explicit min_score ──
captured = {}


class FakeCritic:
    def __init__(self, provider=None, model=None, min_score=None):
        captured["min_score"] = min_score
        self.model = model

    def critique(self, concept):
        return {"passed": True, "score": 9, "reason": "ok"}


from app.services.product_score_service import ProductScoreService
with patch("app.agents.product_viability_critic.ProductViabilityCriticAgent", FakeCritic), \
     patch.object(settings, "PRODUCT_JUDGE_FLOOR", 9):
    ProductScoreService(concept_model="cm", default_model="dm")._judge({"product_name": "x"}, "cm")
check("5-2 judge built with explicit min_score", captured.get("min_score") == 9)

# ── 5-4: recent products loaded BEFORE research/intel ──
order = []
with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()

import app.services.trend_data_service as tds
from app.services.task_service import TaskService


def recent(*a, **k):
    order.append("recent")
    return []


def research(*a, **k):
    order.append("research")
    return "r"


with patch.object(TaskService, "recent_product_titles", side_effect=recent), \
     patch.object(agent, "_load_insights_block", return_value=""), \
     patch.object(tds.TrendDataService, "get_real_trend_signals",
                  side_effect=lambda: order.append("trends") or {"rising_queries": {}, "interest_trend": {}}), \
     patch.object(agent._research, "research", side_effect=research), \
     patch.object(agent._intelligence, "synthesize", return_value={"opportunities": [], "confidence": "low"}):
    agent.run()
check(f"5-4 recent products loaded before research (order={order})",
      "recent" in order and order.index("recent") < order.index("research"))

# ── 5-8: rule_version + floors in the concept_scored payload ──
from datetime import date
recorded = {}
with patch("app.agents.product_viability_critic.ProductViabilityCriticAgent", FakeCritic), \
     patch.object(ProductScoreService, "_record", staticmethod(lambda concept, result: recorded.update(result))):
    ProductScoreService(concept_model="cm", default_model="dm").score(
        {"product_name": "Test Print", "product_format": "single_print", "description": "test print",
         "market": {"competition_count": 500, "price_p50": 7.0}},
        trend_data={"interest_trend": {"printable wall art": {"direction": "rising"}}},
        recent_titles=[], today=date(2026, 7, 13))
check("5-8 result carries rule_version=2", recorded.get("rule_version") == 2)
check("5-8 result carries floors", isinstance(recorded.get("floors"), dict) and "judge" in recorded["floors"])

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-106-G tests passed.")
