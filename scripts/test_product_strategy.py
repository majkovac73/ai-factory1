"""
Product-strategy tests: theme-diversity guard + deeper descriptions.

Usage: python scripts/test_product_strategy.py
"""
import os, sys
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.agents.trend_research_agent import TrendResearchAgent

def agent_with(products):
    a = TrendResearchAgent.__new__(TrendResearchAgent)
    a._recent_products = products
    return a

# monoculture: 10 back-to-school products -> flags 'school'/'teacher', demands diversify
mono = [(f"Back to School Teacher Planner {i}", "pdf_planner_or_guide") for i in range(7)] + \
       [(f"Classroom Teacher Sticker Sheet {i}", "sticker_sheet_design") for i in range(3)]
with patch.object(settings, "THEME_SATURATION_PCT", 0.25):
    blk = agent_with(mono)._theme_diversity_block()
check("monoculture: block present", "THEME SATURATION" in blk)
check("monoculture: names 'school'", "'school'" in blk)
check("monoculture: names 'teacher'", "'teacher'" in blk)
check("monoculture: demands a different theme", "COMPLETELY DIFFERENT" in blk)
check("monoculture: does NOT flag generic format words (planner/sticker)",
      "'planner'" not in blk and "'sticker'" not in blk)

# diverse shop -> no saturation block
diverse = [("Plant Care Journal", "pdf_planner_or_guide"),
           ("Wedding Seating Chart", "single_print"),
           ("Halloween Coloring Page", "coloring_page"),
           ("Camping Checklist", "pdf_planner_or_guide"),
           ("Boho Nursery Wall Art", "single_print"),
           ("Recipe Card Template", "greeting_card_design"),
           ("Budget Tracker", "pdf_planner_or_guide"),
           ("Yoga Pose Poster", "single_print"),
           ("Dog Mom Sticker Sheet", "sticker_sheet_design"),
           ("Travel Bucket List", "pdf_planner_or_guide")]
check("diverse shop: no saturation block", agent_with(diverse)._theme_diversity_block() == "")

# too few products -> no block (can't judge)
check("too few products: no block", agent_with(diverse[:5])._theme_diversity_block() == "")

# empty -> no block, no crash
check("empty: no block", agent_with([])._theme_diversity_block() == "")

# block is actually injected into the concept prompt
with patch.object(settings, "THEME_SATURATION_PCT", 0.25):
    a = agent_with(mono)
    a._insights_block = ""
    a._trend_data = {}
    a._seasonal_mode = None
    prompt = a._build_concept_prompt("some market insight", "")
check("prompt includes the diversity block", "THEME SATURATION" in prompt)

# ── deeper descriptions: SEO prompt now demands length + structure ────────────
from app.agents.etsy.seo_generator import SEOGeneratorAgent
gen = SEOGeneratorAgent.__new__(SEOGeneratorAgent)
captured = {}
gen._generate = lambda p: captured.setdefault("prompt", p) or '{"title":"t","description":"d","keywords":["a","b","c","d","e"],"sections":[]}'
from app.core.validation.schema_validator import SchemaValidator
gen.validator = SchemaValidator()
gen.sanitizer = __import__("app.core.utils.json_sanitizer", fromlist=["JSONSanitizer"]).JSONSanitizer()
gen.generate_seo({"product_name": "Plant Care Journal", "concept": "x", "target_audience": "plant lovers"}, "")
p = captured["prompt"]
check("desc prompt requires word/char length", "130-200 word" in p or "700-1100 char" in p.replace("characters", "char"))
check("desc prompt requires WHAT'S INCLUDED", "WHAT'S INCLUDED" in p)
check("desc prompt requires instant digital download note", "INSTANT DIGITAL DOWNLOAD" in p)
check("desc prompt requires primary keyword in first sentence", "PRIMARY keyword" in p)
check("desc prompt forbids physical shipping", "physical shipping" in p)

# ── demand axis: Etsy-market proxy when Google Trends is blind (niches) ───────
from app.services.product_score_service import ProductScoreService as _PS
def dem(cc, it=None):
    return _PS._demand({"product_name": "Niche Widget", "description": "x", "market": {"competition_count": cc} if cc is not None else {}}, {"interest_trend": it or {}})
check("demand: proven niche (800 rivals, no trend) -> 7", dem(800)[0] == 7)
check("demand: unproven (0 rivals) -> 3", dem(0)[0] == 3)
check("demand: no market data -> 4", dem(None)[0] == 4)
check("demand: saturated (200k rivals) -> 6", dem(200000)[0] == 6)
check("demand: rising Trends keyword still wins (10)",
      _PS._demand({"product_name": "coloring pages cat", "description": "x", "market": {"competition_count": 800}},
                  {"interest_trend": {"coloring pages": {"direction": "rising"}}})[0] == 10)
check("demand: falling Trends keyword -> 0 (not overridden by market)",
      _PS._demand({"product_name": "fidget spinner", "description": "x", "market": {"competition_count": 800}},
                  {"interest_trend": {"fidget spinner": {"direction": "falling"}}})[0] == 0)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All product-strategy tests passed.")
