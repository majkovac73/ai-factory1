"""
Two fixes verified here:

1. COST: ProductScoreService skips BOTH paid LLM judge calls when the FREE
   deterministic score already can't clear PRODUCT_DET_FLOOR (or an axis is at
   its floor). This never changes an outcome — a det-failing concept can't pass
   anyway — it just stops paying sonnet-5 to grade the doomed. This was the bulk
   of the 07-22 overspend (146 concepts scored -> ~292 judge calls).

2. POD CRASH: EtsyShippingService.get_readiness_state_id() resolves the shop's
   readiness_state_id (Etsy now REQUIRES it on physical listings) preferring the
   made_to_order state. Listing failure -> task blocked + marketing suppressed
   (checked via the orchestrator flow contract in test_pod_shipping_guard).

Usage: python scripts/test_pod_readiness_and_cost.py
"""
import os, sys, asyncio, tempfile
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pr.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest.mock import patch
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.services.product_score_service import ProductScoreService

# ── 1) cost pre-gate: judges are NOT called when det floor is unreachable ──────
svc = ProductScoreService()

judge_calls = {"n": 0}
def _spy_judge(self, concept, model):
    judge_calls["n"] += 1
    return {"score": 10, "reason": "perfect"}

# A concept with weak deterministic evidence: 0 rivals (unproven) + falling-ish.
# competition axis / demand axis at floor -> det fails -> judges MUST be skipped.
weak = {"product_name": "Random Doodle Thing", "description": "x",
        "product_format": "single_print", "market": {"competition_count": 0}}

with patch.object(ProductScoreService, "_judge", _spy_judge), \
     patch.object(settings, "SCORE_SKIP_JUDGE_ON_DET_FAIL", True):
    res = svc.score(weak, trend_data={"interest_trend": {}}, record=False)
check("weak concept -> did NOT pass", res["passed"] is False)
check("weak concept -> judges SKIPPED (0 judge calls)", judge_calls["n"] == 0)
check("weak concept -> result flags judge_skipped", "judge_skipped" in res)
check("weak concept -> retry_feedback explains pre-judge reject", "pre-judge reject" in res["retry_feedback"])

# When the guard is OFF, judges run again (behaviour is opt-out-able).
judge_calls["n"] = 0
with patch.object(ProductScoreService, "_judge", _spy_judge), \
     patch.object(settings, "SCORE_SKIP_JUDGE_ON_DET_FAIL", False):
    svc.score(weak, trend_data={"interest_trend": {}}, record=False)
check("guard OFF -> judges DO run (2 calls)", judge_calls["n"] == 2)

# A strong-evidence concept must still reach the judges (guard must not over-skip).
judge_calls["n"] = 0
strong = {"product_name": "Pour Over Coffee Ratio Guide Printable", "description": "x",
          "product_format": "pdf_planner_or_guide", "market": {"competition_count": 800, "price_p50": 12.0}}
with patch.object(ProductScoreService, "_judge", _spy_judge), \
     patch.object(settings, "SCORE_SKIP_JUDGE_ON_DET_FAIL", True):
    res2 = svc.score(strong, trend_data={"interest_trend": {"pour over coffee": {"direction": "rising"}}}, record=False)
# strong evidence should clear the det floor and therefore be judged
check("strong-evidence concept -> judges ARE called", judge_calls["n"] == 2)

# ── 1b) judge short-circuit: cheap judge below floor -> skip the expensive one ──
# Make the two models distinct + record per-model calls.
per_model = {}
def _model_judge(self, concept, model):
    per_model[model] = per_model.get(model, 0) + 1
    # default (cheap) model returns a LOW score -> below floor; expensive would be high
    return {"score": 3 if model == svc._default_model else 10, "reason": "r"}

with patch.object(svc, "_concept_model", "anthropic/claude-sonnet-5"), \
     patch.object(svc, "_default_model", "openai/gpt-4o-mini"), \
     patch.object(ProductScoreService, "_judge", _model_judge), \
     patch.object(settings, "SCORE_SKIP_JUDGE_ON_DET_FAIL", True), \
     patch.object(settings, "SCORE_SHORTCIRCUIT_EXPENSIVE_JUDGE", True), \
     patch.object(settings, "PRODUCT_JUDGE_FLOOR", 6):
    res3 = svc.score(strong, trend_data={"interest_trend": {"pour over coffee": {"direction": "rising"}}}, record=False)
check("shortcircuit: cheap judge WAS called", per_model.get("openai/gpt-4o-mini", 0) == 1)
check("shortcircuit: expensive (sonnet-5) judge was SKIPPED", per_model.get("anthropic/claude-sonnet-5", 0) == 0)
check("shortcircuit: concept still fails (cheap judge below floor)", res3["passed"] is False)

# When cheap judge PASSES the floor, the expensive judge MUST run (independence).
per_model.clear()
def _model_judge_hi(self, concept, model):
    per_model[model] = per_model.get(model, 0) + 1
    return {"score": 8, "reason": "r"}  # both above floor
with patch.object(svc, "_concept_model", "anthropic/claude-sonnet-5"), \
     patch.object(svc, "_default_model", "openai/gpt-4o-mini"), \
     patch.object(ProductScoreService, "_judge", _model_judge_hi), \
     patch.object(settings, "PRODUCT_JUDGE_FLOOR", 6):
    svc.score(strong, trend_data={"interest_trend": {"pour over coffee": {"direction": "rising"}}}, record=False)
check("shortcircuit: cheap judge clears floor -> expensive judge RUNS", per_model.get("anthropic/claude-sonnet-5", 0) == 1)

# ── 2) readiness state resolution: prefers made_to_order, caches ───────────────
import app.services.etsy_shipping_service as ess
ess._cached_readiness_id = None
from app.services.etsy_shipping_service import EtsyShippingService

class FakeResp:
    def __init__(self, status, js):
        self.status_code = status; self._js = js; self.text = str(js)
    def json(self): return self._js

DEFS = {"count": 2, "results": [
    {"readiness_state_id": 111, "readiness_state": "ships_in_business_days", "min_processing_days": 1},
    {"readiness_state_id": 1498023031300, "readiness_state": "made_to_order", "min_processing_days": 3},
]}

class FakeClient:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return FakeResp(200, DEFS)

async def _tok(): return "t"

with patch.object(settings, "ETSY_SHOP_ID", 58716525), \
     patch.object(settings, "ETSY_READINESS_STATE_ID", None), \
     patch("app.services.etsy_shipping_service.get_valid_access_token", _tok), \
     patch("httpx.AsyncClient", lambda *a, **k: FakeClient()):
    rid = asyncio.new_event_loop().run_until_complete(EtsyShippingService().get_readiness_state_id())
check("readiness: picks made_to_order id", rid == "1498023031300")

# env override wins and short-circuits (no API call needed)
ess._cached_readiness_id = None
with patch.object(settings, "ETSY_READINESS_STATE_ID", "999"):
    rid2 = asyncio.new_event_loop().run_until_complete(EtsyShippingService().get_readiness_state_id())
check("readiness: env override wins", rid2 == "999")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All POD-readiness + cost-guard tests passed.")
