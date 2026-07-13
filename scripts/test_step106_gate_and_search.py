"""
Step 106-A test — 1-1 reachable floors gate, 1-2 persistent search,
1-3 best-of-pool.

Usage: python scripts/test_step106_gate_and_search.py
"""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s106a.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.models.analytics_event import AnalyticsEvent  # noqa: F401
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── fake judges keyed by model ──
class FakeCritic:
    _scores = {}

    def __init__(self, provider=None, model=None, min_score=None):
        self.model = model

    def critique(self, concept):
        s = FakeCritic._scores.get(self.model, 9)
        return {"passed": s >= 6, "score": s, "reason": f"{self.model}:{s}"}


def set_judges(a, b):
    FakeCritic._scores = {"cm": a, "dm": b}


from app.services.product_score_service import ProductScoreService


def svc():
    return ProductScoreService(concept_model="cm", default_model="dm")


def winner(name="Cottagecore Mushroom Village Wall Art", comp=500, p50=7.0):
    return {"product_name": name, "product_format": "single_print",
            "description": f"A whimsical {name} illustration.",
            "market": {"competition_count": comp, "price_p50": p50}}


TREND = {"interest_trend": {"printable wall art": {"direction": "rising"}}}

with patch("app.agents.product_viability_critic.ProductViabilityCriticAgent", FakeCritic):
    s = svc()

    # 1-1: REACHABLE — B=36ish + dual 9s should PASS (this was impossible before)
    set_judges(9, 9)
    r = s.score(winner(comp=5000), trend_data=TREND, recent_titles=[], today=date(2026, 7, 13), record=False)
    check(f"1-1 dual-9 excellent concept PASSES (total={r['total']}, was impossible at 95)", r["passed"])
    check("1-1 rule_version=2", r["rule_version"] == 2)
    check("1-1 floors recorded", set(r["floors"]) == {"total", "judge", "det", "axis"})

    # judge floor: B perfect but one judge 8 -> FAIL (harsher=8 < 9)
    set_judges(10, 8)
    r = s.score(winner(comp=500), trend_data=TREND, recent_titles=[], today=date(2026, 7, 13), record=False)
    check(f"1-1 judge floor: 10/8 fails (total={r['total']})", not r["passed"] and not r["floors"]["judge"])

    # det floor: dual 10s but weak evidence (falling demand + saturated) -> FAIL det
    set_judges(10, 10)
    weak = winner(comp=90000, p50=1.0)
    fall = {"interest_trend": {"printable wall art": {"direction": "falling"}}}
    r = s.score(weak, trend_data=fall, recent_titles=[], today=date(2026, 7, 13), record=False)
    check(f"1-1 det floor: dual-10 + weak evidence fails (total={r['total']})", not r["passed"])
    check("1-1 axis floor tripped by falling demand", not r["floors"]["axis"])

    # default min score is 90
    check("1-1 PRODUCT_MIN_SCORE default 90", getattr(settings, "PRODUCT_MIN_SCORE") == 90)

# ── 1-2 / 1-3: persistent search across insights + best-of-pool ──
from app.agents.trend_research_agent import TrendResearchAgent
agent = TrendResearchAgent.__new__(TrendResearchAgent)
agent._recent_products = []
agent._insights_block = ""
agent._trend_data = {}
agent.MAX_CONCEPT_ATTEMPTS = 3
agent._score_service = None  # replaced per-test

# stub the per-attempt machinery: _generate returns a concept keyed by insight;
# scores are controlled by a fake score service.
concept_by_insight = {
    "insA": {"product_name": "A concept", "product_format": "single_print", "description": "A concept desc"},
    "insB": {"product_name": "B concept", "product_format": "single_print", "description": "B concept desc"},
}


class FakeScore:
    def __init__(self, table):
        self.table = table  # name -> total

    def score(self, data, trend_data=None, recent_titles=None):
        total = self.table.get(data["product_name"], 50)
        passed = total >= 90
        return {"total": total, "passed": passed, "min_score": 90,
                "floors": {}, "judges": {}, "retry_feedback": "try harder"}


import json as _json


def make_agent(score_table, gen_map):
    a = TrendResearchAgent.__new__(TrendResearchAgent)
    a._recent_products = []
    a._insights_block = ""
    a._trend_data = {}
    a.MAX_CONCEPT_ATTEMPTS = 3
    a.sanitizer = type("S", (), {"extract": staticmethod(lambda r: _json.loads(r))})()
    a._score_service = FakeScore(score_table)
    a._generate = lambda prompt: _json.dumps(gen_map(prompt))
    a._build_concept_prompt = lambda insight, feedback: insight
    a._validate_product = lambda d: None
    a._dedup_error = lambda d: None
    a._attach_market = lambda d: None
    return a


# insight A always yields "A concept" (fails, 70); insight B yields "B concept" (passes, 92)
def gen(prompt):
    return dict(concept_by_insight.get(prompt.split("|")[0], {"product_name": "X", "product_format": "single_print", "description": "x"}))


with patch.object(settings, "PRODUCT_SCORE_ENFORCE", True), \
     patch.object(settings, "PRODUCT_MIN_SCORE", 90), \
     patch.object(settings, "CONCEPT_SEARCH_MAX_ATTEMPTS_PER_CYCLE", 15):
    a = make_agent({"A concept": 70, "B concept": 92}, gen)
    # prompt is just the insight string; make generate depend on it
    a._generate = lambda prompt: _json.dumps(concept_by_insight.get(prompt, {"product_name": "?", "product_format": "single_print", "description": "?"}))
    a._build_concept_prompt = lambda insight, feedback: insight
    state = a._new_search_state()
    with patch("random.shuffle", lambda x: None):  # keep order [insA, insB]
        product = a._persistent_search(["insA", "insB"], "low", state)
    check("1-2 persistent search tries insight B after A fails -> returns product", product is not None and product["product_name"] == "B concept")
    check("1-2 both insights were tried", state["insights"] == 2)

    # 1-3 best-of-pool: two passers 91 and 96 -> returns the 96
    concept_by_insight2 = {
        "insA": {"product_name": "P91", "product_format": "single_print", "description": "d"},
        "insB": {"product_name": "P96", "product_format": "single_print", "description": "d"},
    }
    a2 = make_agent({"P91": 91, "P96": 96}, gen)
    a2._generate = lambda prompt: _json.dumps(concept_by_insight2.get(prompt, {"product_name": "?", "product_format": "single_print", "description": "?"}))
    a2._build_concept_prompt = lambda insight, feedback: insight
    st2 = a2._new_search_state()
    with patch("random.shuffle", lambda x: None):
        prod2 = a2._persistent_search(["insA", "insB"], "low", st2)
    # 96 >= 90+5 short-circuits, but it comes second; ensure the 96 wins
    check("1-3 best-of-pool returns the higher scorer (96 over 91)", prod2 is not None and prod2["product_name"] == "P96")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-106-A tests passed.")
