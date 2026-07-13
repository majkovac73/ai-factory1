"""
Step 106-C test — 1-6 judge independence + temperature, 1-7 retry memory,
1-8 text-LLM metering.

Usage: python scripts/test_step106_judges_metering.py
"""
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s106c.db")
_data = tempfile.mkdtemp()
os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(_data, "images")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 1-6: temperature threaded through _generate to the provider ──
from app.agents.base_agent import BaseAgent


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.last_usage = None

    async def generate(self, model, prompt, **kwargs):
        self.calls.append({"model": model, "kwargs": kwargs})
        return "ok"


agent = BaseAgent(provider=FakeLLM(), model="openai/gpt-4o-mini")
agent._generate("hi", temperature=0.2)
check("1-6 temperature is forwarded to the provider", agent.llm.calls[-1]["kwargs"].get("temperature") == 0.2)
agent._generate("hi")  # no temperature → not forwarded
check("1-6 no temperature by default (provider default used)", "temperature" not in agent.llm.calls[-1]["kwargs"])

# 1-6 independence warning fires when both judge models are identical
from app.services.product_score_service import ProductScoreService
ProductScoreService._independence_warned = False
with patch.object(settings, "CONCEPT_MODEL", None), patch.object(settings, "DEFAULT_MODEL", "openai/gpt-4o-mini"):
    with patch("app.services.product_score_service.logger") as lg:
        ProductScoreService()
        warned = any("NOT independent" in str(c) for c in lg.warning.call_args_list)
    check("1-6 warns when judges are the same model", warned)
ProductScoreService._independence_warned = False
with patch("app.services.product_score_service.logger") as lg2:
    ProductScoreService(concept_model="anthropic/claude-sonnet-5", default_model="openai/gpt-4o-mini")
    warned2 = any("NOT independent" in str(c) for c in lg2.warning.call_args_list)
check("1-6 no warning when judges differ", not warned2)

# ── 1-8: text-LLM metering ──
check("1-8 mini model is cheap tier", BaseAgent._text_cost("openai/gpt-4o-mini") == settings.TEXT_LLM_COST_USD)
check("1-8 sonnet is strong tier", BaseAgent._text_cost("anthropic/claude-sonnet-5") == settings.TEXT_LLM_COST_USD_STRONG)
check("1-8 non-mini gpt-4o is strong tier", BaseAgent._text_cost("openai/gpt-4o") == settings.TEXT_LLM_COST_USD_STRONG)

# a _generate call records spend in the ledger
from app.services.autonomy_service import AutonomyService
before = AutonomyService().spend_today()
a2 = BaseAgent(provider=FakeLLM(), model="openai/gpt-4o-mini")
a2._generate("meter me")
after = AutonomyService().spend_today()
check(f"1-8 text call records spend (delta={after-before:.4f})", abs((after - before) - settings.TEXT_LLM_COST_USD) < 1e-6)

# SpendCapExceeded from the breaker propagates out of _generate
from app.services.autonomy_service import SpendCapExceeded


class _AutoOver:
    def assert_within_circuit_breaker(self):
        raise SpendCapExceeded("over")

    def record_spend(self, *a, **k):
        pass


blocked = False
with patch("app.services.autonomy_service.AutonomyService", _AutoOver):
    try:
        BaseAgent(provider=FakeLLM(), model="x")._generate("p")
    except SpendCapExceeded:
        blocked = True
check("1-8 text call refuses past the spend ceiling", blocked)

# ledger OSError must NOT kill a text call
class _AutoBad:
    def assert_within_circuit_breaker(self):
        raise OSError("disk")

    def record_spend(self, *a, **k):
        raise OSError("disk")


ok = False
with patch("app.services.autonomy_service.AutonomyService", _AutoBad):
    try:
        out = BaseAgent(provider=FakeLLM(), model="x")._generate("p")
        ok = out == "ok"
    except Exception:
        ok = False
check("1-8 ledger error is soft (text call proceeds)", ok)

# ── 1-7: retry feedback accumulates rejected concepts ──
from app.agents.trend_research_agent import TrendResearchAgent
state = {"rejected": [{"name": "Boho Sunset Print", "total": 72}, {"name": "Desert Cactus Print", "total": 68}]}
fb = TrendResearchAgent._retry_feedback_with_history(state, "base feedback")
check("1-7 retry prompt lists both prior rejected names",
      "Boho Sunset Print" in fb and "Desert Cactus Print" in fb and "Already rejected" in fb)
# includes the just-rejected concept too
fb2 = TrendResearchAgent._retry_feedback_with_history({"rejected": []}, "base",
                                                      data={"product_name": "New One"}, score={"total": 80})
check("1-7 includes the just-rejected concept", "New One" in fb2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-106-C tests passed.")
