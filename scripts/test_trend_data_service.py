"""
Tests for the real trend-data feed (instructions_real_trend_data.md).

Covers, with doubles (no real network in the 4 required tests):
  [1] TrendDataService.get_real_trend_signals returns the expected structured
      dict from mocked pytrends dataframes.
  [2] When pytrends always raises, get_real_trend_signals raises
      TrendDataFetchError after _MAX_RETRIES attempts per keyword (no partial
      fake data).
  [3] ResearchAgent.research(real_trend_data=...) actually injects the real
      keyword strings into the prompt sent to the LLM (not ignored).
  [4] TrendResearchAgent.run() returns None when the trend fetch raises
      TrendDataFetchError, and ResearchAgent.research is NEVER called (no
      fallback to guessing).

Usage:
  python scripts/test_trend_data_service.py
"""
import os
import sys
import types
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import logging
logging.basicConfig(level=logging.ERROR)

import pandas as pd

import app.services.trend_data_service as tds
from app.services.trend_data_service import TrendDataService, TrendDataFetchError

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nReal trend-data service tests\n")


# ── [1] structured dict from mocked pytrends dataframes ──────────────────────
print("[1] get_real_trend_signals returns the expected structured dict...")

# Speed: don't actually sleep between retries.
tds._BACKOFF_BASE_SECONDS = 0

with patch.object(tds, "TrendReq") as MockTrendReq:
    inst = MockTrendReq.return_value

    def _interest_over_time():
        # one keyword column; latest value is the last row
        return pd.DataFrame({"digital planner": [10, 20, 42]})

    def _related_queries():
        rising = pd.DataFrame({
            "query": ["digital planner 2026", "cute digital planner", "goodnotes planner"],
            "value": [250, 180, 120],
        })
        return {"digital planner": {"rising": rising, "top": None}}

    inst.build_payload.return_value = None
    inst.interest_over_time.side_effect = _interest_over_time
    inst.related_queries.side_effect = _related_queries

    svc = TrendDataService()
    out = svc.get_real_trend_signals(keywords=["digital planner"])

wanted_keys = {"keywords", "rising_queries", "interest_snapshot"}
struct_ok = (
    isinstance(out, dict)
    and wanted_keys <= set(out)
    and out["keywords"] == ["digital planner"]
    and out["interest_snapshot"] == {"digital planner": 42}
    and out["rising_queries"]["digital planner"][:2] == ["digital planner 2026", "cute digital planner"]
)
if struct_ok:
    ok("[1] structured dict with real interest_snapshot + rising_queries")
else:
    fail("[1] structure", f"out={out}")


# ── [2] always-raise -> TrendDataFetchError after retries, no partial data ───
print("[2] pytrends always raising -> TrendDataFetchError after _MAX_RETRIES...")

with patch.object(tds, "TrendReq") as MockTrendReq:
    inst = MockTrendReq.return_value
    calls = {"n": 0}
    def _boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("429 rate limited")
    inst.build_payload.side_effect = _boom

    svc = TrendDataService()
    raised = False
    try:
        svc.get_real_trend_signals(keywords=["svg files"])
    except TrendDataFetchError:
        raised = True
    except Exception as e:
        fail("[2] wrong exception", f"{type(e).__name__}: {e}")

# one keyword × _MAX_RETRIES attempts on build_payload
if raised and calls["n"] == tds._MAX_RETRIES:
    ok(f"[2] raised TrendDataFetchError after exactly {tds._MAX_RETRIES} attempts, no partial fake data")
else:
    fail("[2] retries/raise", f"raised={raised}, build_payload calls={calls['n']} (expected {tds._MAX_RETRIES})")


# ── [3] research() injects the real keywords into the prompt ─────────────────
print("[3] ResearchAgent.research injects real trend keywords into the LLM prompt...")

from app.agents.market_intelligence.research import ResearchAgent

captured = {}
agent = ResearchAgent.__new__(ResearchAgent)  # skip BaseAgent __init__ (no provider/DB)
agent._generate = lambda prompt: captured.__setitem__("prompt", prompt) or "findings"

fake_trend = {
    "keywords": ["digital planner", "svg files"],
    "rising_queries": {"digital planner": ["undated digital planner 2026"], "svg files": ["layered svg files"]},
    "interest_snapshot": {"digital planner": 77, "svg files": 41},
}
result = agent.research("Etsy trends", "trends", real_trend_data=fake_trend)
prompt = captured.get("prompt", "")
injected = (
    "undated digital planner 2026" in prompt
    and "layered svg files" in prompt
    and "77" in prompt
    and "Real Google Trends data" in prompt
)
if result == "findings" and injected:
    ok("[3] the real rising queries + interest numbers appear in the prompt")
else:
    fail("[3] injection", f"injected={injected}, prompt_snip={prompt[:160]!r}")


# ── [4] run() returns None on fetch failure; research never called ───────────
print("[4] TrendResearchAgent.run() aborts (None) on fetch failure, no guessing fallback...")

from app.agents.trend_research_agent import TrendResearchAgent

tra = TrendResearchAgent.__new__(TrendResearchAgent)   # skip BaseAgent __init__
research_calls = {"n": 0}
tra._research = types.SimpleNamespace(research=lambda *a, **k: research_calls.__setitem__("n", research_calls["n"] + 1) or "x")
tra._intelligence = types.SimpleNamespace(synthesize=lambda *a, **k: {"opportunities": ["x"]})

class _FailingService:
    def get_real_trend_signals(self, *a, **k):
        raise TrendDataFetchError("blocked / rate limited")

with patch("app.services.trend_data_service.TrendDataService", _FailingService):
    ret = tra.run()

if ret is None and research_calls["n"] == 0:
    ok("[4] run() returned None and never called research (no fallback to guessed data)")
else:
    fail("[4] abort", f"ret={ret}, research_calls={research_calls['n']}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")
sys.exit(0 if _failed == 0 else 1)
