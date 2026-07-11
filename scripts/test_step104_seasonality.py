"""
Step 104-A test — seasonality engine: real dates (1-1), per-event windows (1-2),
out-of-season hard gate + dated prompts (1-3), coloring-page rule (1-8).

Usage: python scripts/test_step104_seasonality.py
"""
import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.seasonality import (
    easter, nth_weekday, upcoming_occasions, occasion_mismatch, seasonal_prompt_block,
)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# 1-1: computed movable dates
check("1-1 Easter 2026 = Apr 5", easter(2026) == date(2026, 4, 5))
check("1-1 Easter 2027 = Mar 28", easter(2027) == date(2027, 3, 28))
check("1-1 Thanksgiving 2026 = Nov 26", nth_weekday(2026, 11, 3, 4) == date(2026, 11, 26))
check("1-1 Father's Day 2026 = Jun 21", nth_weekday(2026, 6, 6, 3) == date(2026, 6, 21))
check("1-1 Mother's Day 2026 = May 10", nth_weekday(2026, 5, 6, 2) == date(2026, 5, 10))

# 1-2: per-event windows
def keys(today):
    return {o["key"] for o in upcoming_occasions(today)}

check("1-2 Sep 20 -> Christmas IN window", "christmas" in keys(date(2026, 9, 20)))
check("1-2 Nov 20 -> Christmas NOT in window", "christmas" not in keys(date(2026, 11, 20)))
check("1-2 Apr 20 -> Mother's Day NOT (<=3 weeks)", "mothers_day" not in keys(date(2026, 4, 20)))
check("1-2 min window never below 4 weeks (except new year)",
      all(o["days_until"] >= 28 for o in upcoming_occasions(date(2026, 5, 1)) if o["key"] != "new_year"))

# 1-3: hard gate
check("1-3 Christmas concept rejected on Jul 11",
      occasion_mismatch("Christmas Gift Tags Printable", "", date(2026, 7, 11)) is not None)
check("1-3 Christmas concept accepted on Oct 10",
      occasion_mismatch("Christmas Gift Tags Printable", "", date(2026, 10, 10)) is None)
check("1-3 Graduation concept rejected on Jul 11",
      occasion_mismatch("Graduation Cap Print", "class of 2026", date(2026, 7, 11)) is not None)
check("1-3 evergreen concept NOT flagged",
      occasion_mismatch("Cottagecore Mushroom Wall Art", "cozy forest print", date(2026, 7, 11)) is None)

# 1-3: negative prompt block
block = seasonal_prompt_block(date(2026, 7, 11))
check("1-3 prompt block has SEASONAL TIMING", "SEASONAL TIMING" in block)
check("1-3 prompt block has a negative 'do NOT' list", "Do NOT" in block)

# 1-3: wired into _validate_product
with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()
with patch("app.core.seasonality.occasion_mismatch", return_value="out of season"):
    err = agent._validate_product({
        "product_name": "Halloween Spooky Print", "product_format": "single_print",
        "description": "A Halloween Spooky Print for the season.",
    })
check("1-3 _validate_product returns the season error", err == "out of season")

# 1-8: coloring-page delivery brief augmentation is present in the pipeline source
import inspect
import app.services.pipeline_orchestrator as po
src = inspect.getsource(po.PipelineOrchestrator._stage_pod_design)
check("1-8 coloring-page uncolored rule present", "STRICT COLORING-PAGE RULES" in src and "COMPLETELY WHITE" in src)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-A seasonality tests passed.")
