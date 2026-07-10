"""
Step 102 test — ProductViabilityCriticAgent + its wiring into TrendResearchAgent.

Covers the Testing section of instructions_general_viability_critic_and_audit.md:
  [1] critique() parses a passing critique correctly.
  [2] critique() parses a failing critique correctly.
  [3] critique() fails CLOSED (passed=False) when the LLM output is unparseable.
  [4] critique() extracts JSON from noisy (code-fenced / prose-wrapped) output.
  [5] TrendResearchAgent._propose_product: a concept that is schema-valid but
      fails the viability critic ONCE then passes consumes a retry attempt and
      feeds the critic's reason into the next concept prompt as feedback.
  [6] A concept that keeps failing the critic across all attempts -> None
      (no task ever created from a non-viable concept).

No real LLM calls — provider + critic are mocked. No cost.

Usage:
  python scripts/test_step102_viability_critic.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.product_viability_critic import ProductViabilityCriticAgent
from app.agents.trend_research_agent import TrendResearchAgent

failures = []


def check(name, cond):
    if cond:
        print(f"[PASS] {name}")
    else:
        print(f"[FAIL] {name}")
        failures.append(name)


# A critic instance that never touches a real provider.
def make_critic():
    with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
        return ProductViabilityCriticAgent()


# ---- [1] passing critique ----
critic = make_critic()
with patch.object(critic, "_generate", return_value='{"passed": true, "score": 8, "reason": "Specific, buildable, clear buyer."}'):
    res = critic.critique({"product_name": "Plant Care Weekly Planner"})
check("1 passing critique parsed", res["passed"] is True and res["score"] == 8 and "buyer" in res["reason"].lower())

# ---- [2] failing critique ----
with patch.object(critic, "_generate", return_value='{"passed": false, "score": 2, "reason": "Generic phrase on a plain background; no craft."}'):
    res = critic.critique({"product_name": "Motivation"})
check("2 failing critique parsed", res["passed"] is False and res["score"] == 2 and "generic" in res["reason"].lower())

# ---- [3] unparseable -> fail closed ----
with patch.object(critic, "_generate", return_value="the model refused and wrote prose with no json at all"):
    res = critic.critique({"product_name": "Whatever"})
check("3 unparseable fails closed", res["passed"] is False and res["score"] == 0 and "could not be parsed" in res["reason"])

# ---- [4] noisy/code-fenced output extracted ----
noisy = 'Here is my judgment:\n```json\n{"passed": true, "score": 7, "reason": "Good hook."}\n```\nHope that helps.'
with patch.object(critic, "_generate", return_value=noisy):
    res = critic.critique({"product_name": "Constellation Map Print"})
check("4 noisy output extracted", res["passed"] is True and res["score"] == 7)


# ---- [5] + [6] wiring into TrendResearchAgent._propose_product ----
def make_agent():
    with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
        # ResearchAgent / IntelligenceAgent / critic constructors also hit BaseAgent
        return TrendResearchAgent()


valid_concept_json = (
    '{"product_name": "Plant Parent Weekly Care Planner", '
    '"product_format": "pdf_planner_or_guide", "page_count": 5, '
    '"description": "The Plant Parent Weekly Care Planner helps track watering and light.", '
    '"target_audience": "houseplant owners", "confidence": "medium"}'
)

# [5] critic fails once, then passes -> second attempt returned; feedback threaded.
agent = make_agent()
prompts_seen = []


def fake_generate(prompt):
    prompts_seen.append(prompt)
    return valid_concept_json


critique_results = [
    {"passed": False, "score": 3, "reason": "Too generic; add a real hook."},
    {"passed": True, "score": 8, "reason": "Solid recurring-task planner."},
]

with patch.object(agent, "_generate", side_effect=fake_generate), \
     patch.object(agent._critic, "critique", side_effect=critique_results):
    result = agent._propose_product("houseplant care insight", "low")

check("5 fail-then-pass returns a concept", result is not None and result["product_name"] == "Plant Parent Weekly Care Planner")
check("5 consumed exactly 2 attempts", len(prompts_seen) == 2)
check("5 critic reason fed back as feedback in 2nd prompt",
      len(prompts_seen) == 2 and "Too generic; add a real hook." in prompts_seen[1]
      and "commercially viable" in prompts_seen[1])

# [6] critic always fails -> None after MAX_CONCEPT_ATTEMPTS, no concept escapes.
agent2 = make_agent()
attempts = []

with patch.object(agent2, "_generate", side_effect=lambda p: attempts.append(p) or valid_concept_json), \
     patch.object(agent2._critic, "critique",
                  return_value={"passed": False, "score": 1, "reason": "Not viable."}):
    result2 = agent2._propose_product("weak insight", "low")

check("6 always-fail critic yields None", result2 is None)
check("6 exhausted all MAX_CONCEPT_ATTEMPTS", len(attempts) == TrendResearchAgent.MAX_CONCEPT_ATTEMPTS)


print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 viability-critic tests passed.")
