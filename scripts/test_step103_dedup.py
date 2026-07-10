"""
Step 103 / A-3 test — concept dedup against recent shop products.

  [1] _dedup_error flags a near-duplicate of the SAME format, ignores different
      formats and different themes.
  [2] _build_concept_prompt lists existing products when recent_products is set.
  [3] _propose_product: a duplicate concept consumes a retry with dedup feedback,
      then a clearly-different concept passes.

Usage: python scripts/test_step103_dedup.py
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

agent._recent_products = [
    ("Boho Sunset Wall Art Print", "single_print"),
    ("Weekly Budget Planner", "pdf_planner_or_guide"),
]

# [1] dedup detection
near_dup = {"product_name": "Boho Sunset Wall Art Poster", "product_format": "single_print"}
check("1 near-duplicate same format flagged", agent._dedup_error(near_dup) is not None)

diff_format = {"product_name": "Boho Sunset Wall Art Print", "product_format": "coloring_page"}
check("1 same name different format NOT flagged", agent._dedup_error(diff_format) is None)

diff_theme = {"product_name": "Cottagecore Mushroom Coloring Page", "product_format": "coloring_page"}
check("1 different theme NOT flagged", agent._dedup_error(diff_theme) is None)

# [2] prompt lists existing products
prompt = agent._build_concept_prompt("some insight", "")
check("2 prompt lists existing products", "ALREADY in the shop" in prompt and "Weekly Budget Planner" in prompt)

# [3] duplicate then unique
valid_dup = (
    '{"product_name": "Boho Sunset Wall Art Poster", "product_format": "single_print", '
    '"description": "The Boho Sunset Wall Art Poster brings warm desert tones indoors.", '
    '"target_audience": "boho decor fans", "confidence": "medium"}'
)
valid_unique = (
    '{"product_name": "Midnight Forest Constellation Print", "product_format": "single_print", '
    '"description": "The Midnight Forest Constellation Print maps stars over a pine forest.", '
    '"target_audience": "stargazers", "confidence": "medium"}'
)
responses = [valid_dup, valid_unique]
prompts_seen = []


def fake_gen(p):
    prompts_seen.append(p)
    return responses[len(prompts_seen) - 1]


with patch.object(agent, "_generate", side_effect=fake_gen), \
     patch.object(agent._critic, "critique", return_value={"passed": True, "score": 8, "reason": "good"}):
    result = agent._propose_product("insight", "low")

check("3 returns the unique (2nd) concept", result is not None and "Constellation" in result["product_name"])
check("3 duplicate consumed a retry (2 prompts)", len(prompts_seen) == 2)
check("3 dedup feedback fed into 2nd prompt", "too similar" in prompts_seen[1].lower())

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 dedup tests passed.")
