"""
Step 103 / C-1 test — trademark / IP screening (existential-risk gate).

  [1] find_trademark catches brand/character/celebrity terms (whole-word),
      and does NOT false-positive on clean creative concepts.
  [2] TrendResearchAgent._validate_product rejects a trademarked concept with
      actionable feedback; a clean concept passes.
  [3] filter_tags drops trademarked tags, keeps clean ones.
  [4] filter_queries drops poisoned rising queries.
  [5] TRADEMARK_BLOCKLIST_EXTRA env extends the list.

Usage: python scripts/test_step103_trademark.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import trademark_screen as tm
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] detection + no false positives
check("1 catches 'bluey'", tm.find_trademark("Cute Bluey Coloring Page") == "bluey")
check("1 catches 'taylor swift'", tm.find_trademark("taylor swift lyrics print") == "taylor swift")
check("1 catches 'stanley cup'", tm.find_trademark("stanley cup accessories") == "stanley cup")
check("1 clean concept passes", tm.find_trademark("Cottagecore Mushroom Village Coloring Page") is None)
check("1 'art' not matched inside 'artist'", tm.find_trademark("abstract artist wall print") is None)
check("1 'nike' not matched inside 'niken'", tm.find_trademark("niken pattern") is None)

# [2] concept validation gate
with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()

bad = {
    "product_name": "Bluey Birthday Coloring Page",
    "product_format": "coloring_page",
    "description": "A fun Bluey Birthday Coloring Page for kids.",
}
err = agent._validate_product(bad)
check("2 trademarked concept rejected", err is not None and "trademark" in err.lower())

good = {
    "product_name": "Woodland Fox Birthday Coloring Page",
    "product_format": "coloring_page",
    "description": "A charming Woodland Fox Birthday Coloring Page with forest friends.",
}
check("2 clean concept passes validation", agent._validate_product(good) is None)

# [3] tag filtering
clean, dropped = tm.filter_tags(["boho wall art", "pokemon cards", "cute print", "nike swoosh"])
check("3 clean tags kept", "boho wall art" in clean and "cute print" in clean)
check("3 trademarked tags dropped", set(dropped) == {"pokemon cards", "nike swoosh"})

# [4] query filtering
qs = tm.filter_queries(["minimalist planner", "taylor swift wallpaper", "budget tracker"])
check("4 poisoned query dropped", "taylor swift wallpaper" not in qs and "minimalist planner" in qs)

# [5] env extension
with patch.object(settings, "TRADEMARK_BLOCKLIST_EXTRA", ["acmecorp"]):
    check("5 env-extra term blocked", tm.find_trademark("acmecorp mug") == "acmecorp")
check("5 env-extra not blocked once removed", tm.find_trademark("acmecorp mug") is None)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 trademark tests passed.")
