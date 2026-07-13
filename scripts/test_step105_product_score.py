"""
Step 105-B test — 1-1 ProductScoreService composite 0-100 gate.

Usage: python scripts/test_step105_product_score.py
"""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s105b.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.models.analytics_event import AnalyticsEvent  # noqa: F401 (register table)
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── fake judges: score keyed by model so we control call1 vs call2 ──
class FakeCritic:
    _scores = {}  # model -> score

    def __init__(self, provider=None, model=None, min_score=None):
        self.model = model

    def critique(self, concept):
        s = FakeCritic._scores.get(self.model, 9)
        return {"passed": s >= 6, "score": s, "reason": f"model {self.model} says {s}"}


def set_judges(concept_score, default_score):
    FakeCritic._scores = {"concept-m": concept_score, "default-m": default_score}


from app.services.product_score_service import ProductScoreService


def make_service():
    return ProductScoreService(concept_model="concept-m", default_model="default-m")


# a strong evergreen concept with rising demand + low competition + good price
def winner():
    return {
        "product_name": "Cottagecore Mushroom Village Wall Art",
        "product_format": "single_print",
        "description": "A whimsical Cottagecore Mushroom Village Wall Art illustration.",
        "market": {"competition_count": 500, "price_p50": 7.0},
    }


TREND = {"interest_trend": {"printable wall art": {"direction": "rising", "interest_now": 80, "interest_prev": 60}}}
# note: 'wall' overlaps the concept -> demand matches 'printable wall art' rising

with patch("app.agents.product_viability_critic.ProductViabilityCriticAgent", FakeCritic):
    svc = make_service()

    # (a) obvious winner + two 10s -> 100, passes
    set_judges(10, 10)
    r = svc.score(winner(), trend_data=TREND, recent_titles=[], today=date(2026, 7, 12), record=False)
    check(f"1-1a winner+two 10s passes (total={r['total']})", r["passed"] and r["total"] >= 95)
    check("1-1a deterministic demand rising=10", r["deterministic"]["demand"]["points"] == 10)
    check("1-1a deterministic competition low=10", r["deterministic"]["competition"]["points"] == 10)
    check("1-1a price upper-half=10", r["deterministic"]["price"]["points"] == 10)
    check("1-1a timing evergreen=5", r["deterministic"]["timing"]["points"] == 5)

    # (b) same winner but one judge says 7 -> harsher judge dominates -> fails
    set_judges(10, 7)
    r = svc.score(winner(), trend_data=TREND, recent_titles=[], today=date(2026, 7, 12), record=False)
    check(f"1-1b one 7 judge fails (total={r['total']})", not r["passed"] and r["total"] < 95)
    check("1-1b uses harsher judge (7)", r["judges"]["harsher"] == 7)

    # (c1) falling keyword -> demand 0 -> fails regardless of two 10s
    set_judges(10, 10)
    fall = {"interest_trend": {"printable wall art": {"direction": "falling"}}}
    r = svc.score(winner(), trend_data=fall, recent_titles=[], today=date(2026, 7, 12), record=False)
    check(f"1-1c falling demand fails even with two 10s (total={r['total']})", not r["passed"])
    check("1-1c demand scored 0", r["deterministic"]["demand"]["points"] == 0)

    # (c2) >50k competition -> comp 2 -> fails
    saturated = winner()
    saturated["market"] = {"competition_count": 84000, "price_p50": 7.0}
    r = svc.score(saturated, trend_data=TREND, recent_titles=[], today=date(2026, 7, 12), record=False)
    check(f"1-1c saturated competition fails (total={r['total']})", not r["passed"])
    check("1-1c competition scored 2", r["deterministic"]["competition"]["points"] == 2)

    # (d) hard gate: trademark -> 0, no judge calls
    tm = winner()
    tm["product_name"] = "Disney Castle Wall Art"
    tm["description"] = "A Disney castle themed wall art print."
    r = svc.score(tm, trend_data=TREND, recent_titles=[], today=date(2026, 7, 12), record=False)
    check(f"1-1d trademark hard-gate -> 0 (total={r['total']})", r["total"] == 0 and not r["passed"])
    check("1-1d hard gate reason present", "hard_gate" in r and r["hard_gate"])

    # (d2) out-of-season occasion hard-gate -> 0
    oos = {
        "product_name": "Halloween Spooky Ghost Coloring Page",
        "product_format": "coloring_page",
        "description": "A Halloween spooky ghost coloring page.",
        "market": {"competition_count": 500, "price_p50": 3.5},
    }
    r = svc.score(oos, trend_data=TREND, recent_titles=[], today=date(2026, 7, 12), record=False)
    check(f"1-1d out-of-season Halloween in July -> 0 (total={r['total']})", r["total"] == 0)

    # originality: near-duplicate title penalized
    set_judges(10, 10)
    r = svc.score(winner(), trend_data=TREND,
                  recent_titles=["Cottagecore Mushroom Village Wall Art Print"],
                  today=date(2026, 7, 12), record=False)
    check("1-1 originality penalizes near-dup (<=1)", r["deterministic"]["originality"]["points"] <= 1)

    # retry feedback names the weakest axis
    set_judges(10, 10)
    r = svc.score(saturated, trend_data=fall, recent_titles=[], today=date(2026, 7, 12), record=False)
    check("1-1 retry feedback names competition or demand",
          "competition" in r["retry_feedback"] or "demand" in r["retry_feedback"])

    # records a concept_scored event
    from app.services.analytics_service import AnalyticsService
    svc.score(winner(), trend_data=TREND, recent_titles=[], today=date(2026, 7, 12), record=True)
    evs = AnalyticsService().get_events(event_type="concept_scored", limit=10)
    check("1-1E concept_scored event recorded", len(evs) >= 1)

# ── shadow mode: PRODUCT_SCORE_ENFORCE default false ──
check("1-1E PRODUCT_SCORE_ENFORCE defaults false (shadow)", getattr(settings, "PRODUCT_SCORE_ENFORCE") is False)
# STEP106 1-1: 95 was mathematically unreachable; the floors-based rule uses 90.
check("1-1 PRODUCT_MIN_SCORE default 90 (106 recalibration)", getattr(settings, "PRODUCT_MIN_SCORE") == 90)

# ── MAX_CONCEPT_ATTEMPTS bumped to 5 ──
from app.agents.trend_research_agent import TrendResearchAgent
check("1-1 MAX_CONCEPT_ATTEMPTS bumped to 5", TrendResearchAgent.MAX_CONCEPT_ATTEMPTS == 5)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-105-B tests passed.")
