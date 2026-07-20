"""
Audit 2026-07-20 #17 — concept generator biased toward higher-margin formats.

_margin_guidance_block ranks proposable formats by net-per-sale (best first) and
de-prioritizes the low-margin saturated formats. Verifies planners rank above
coloring pages and the avoid-list wording is present.

Usage: python scripts/test_audit_step17_margin_guidance.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.trend_research_agent import TrendResearchAgent
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


agent = TrendResearchAgent.__new__(TrendResearchAgent)
block = agent._margin_guidance_block()

check("guidance block present", "MARGIN GUIDANCE" in block)
check("names net-per-sale per format", "/sale)" in block)
check("de-prioritizes coloring_page", "coloring_page" in block)
# planner should appear before coloring_page in the ranked (best-first) list
pi, ci = block.find("pdf_planner_or_guide"), block.find("coloring_page (~$")
check("planner ranks above coloring page (higher margin first)", pi != -1 and (ci == -1 or pi < ci))
check("avoid wording present", "Only propose a low-margin format" in block)

# with the avoid list emptied, no avoid clause
import unittest.mock as mock
with mock.patch.object(settings, "LOW_MARGIN_DEPRIORITIZE_FORMATS", []):
    block2 = agent._margin_guidance_block()
check("empty avoid-list -> no avoid clause", "Only propose a low-margin format" not in block2 and "MARGIN GUIDANCE" in block2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#17 margin-guidance tests passed.")
