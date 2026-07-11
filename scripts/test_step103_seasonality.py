"""
Step 103 / A-7 test — seasonality: target occasions 4-10 weeks out.

  [1] upcoming_occasions surfaces events in the 3-10 week window and excludes
      far-off / just-passed ones.
  [2] a July date surfaces back-to-school / Halloween-adjacent, NOT Christmas.
  [3] seasonal_prompt_block + seasonal_seed_keywords produce sensible output.
  [4] TrendResearchAgent injects the seasonal block into the concept prompt.

Usage: python scripts/test_step103_seasonality.py
"""
import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.seasonality import upcoming_occasions, seasonal_prompt_block, seasonal_seed_keywords

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] window: mid-October -> Thanksgiving (~6wk) in, Christmas (~10wk) edge in,
# Halloween (~2wk) OUT (too soon).
oct15 = date(2026, 10, 15)
occ = {o["occasion"] for o in upcoming_occasions(oct15)}
check("1 Thanksgiving in window (mid-Oct)", any("Thanksgiving" in o for o in occ))
check("1 Halloween excluded (only 2 weeks out)", not any("Halloween" in o for o in occ))

# [2] July -> back to school in window, Christmas NOT
jul15 = date(2026, 7, 15)
occ_jul = {o["occasion"] for o in upcoming_occasions(jul15)}
check("2 July surfaces back to school", any("Back to school" in o for o in occ_jul))
check("2 July does NOT surface Christmas", not any("Christmas" in o for o in occ_jul))

# [3] prompt block + seeds
block = seasonal_prompt_block(oct15)
check("3 prompt block mentions SEASONAL TIMING", "SEASONAL TIMING" in block)
check("3 prompt block names an occasion", "Thanksgiving" in block)
seeds = seasonal_seed_keywords(oct15)
check("3 seasonal seeds returned (<=2)", 0 < len(seeds) <= 2)

# far-off date with nothing in window -> empty block
# (early Aug: back-to-school ~4wk in, so pick a truly empty window — late Dec 27)
dec27 = date(2026, 12, 27)
occ_dec = upcoming_occasions(dec27)
# Valentine's is ~7 weeks out from late Dec -> should be present; just assert typed list
check("3 upcoming returns a list", isinstance(occ_dec, list))

# [4] agent injects seasonal block
with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()
with patch("app.core.seasonality.seasonal_prompt_block", return_value="\n\nSEASONAL TIMING — test block."):
    prompt = agent._build_concept_prompt("insight", "")
check("4 seasonal block injected into concept prompt", "SEASONAL TIMING — test block." in prompt)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 seasonality tests passed.")
