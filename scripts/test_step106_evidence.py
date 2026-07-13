"""
Step 106-B test — 1-4 demand matches rising queries, 1-5 market query normalize.

Usage: python scripts/test_step106_evidence.py
"""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s106b.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 1-4: demand matches rising queries ──
from app.services.product_score_service import ProductScoreService
svc = ProductScoreService(concept_model="cm", default_model="dm")

concept = {"product_name": "Capybara Coloring Book Page", "product_format": "coloring_page",
           "description": "A cute capybara coloring page for kids."}
trend = {"rising_queries": {"coloring pages": ["capybara coloring page", "axolotl coloring page"]},
         "interest_trend": {"coloring pages": {"direction": "flat"}}}
pts, why, rq = svc._demand(concept, trend)
check(f"1-4 rising-query match scores demand 10 (was 6 flat): {pts}", pts == 10)
check("1-4 reason names the rising query", "capybara" in why.lower())
check("1-4 returns the matched rising query", rq == "capybara coloring page")

# no rising match -> falls back to seed direction
concept2 = {"product_name": "Coloring Pages For Adults", "product_format": "coloring_page",
            "description": "adult coloring pages"}
pts2, why2, rq2 = svc._demand(concept2, {"interest_trend": {"coloring pages": {"direction": "flat"}}, "rising_queries": {}})
check("1-4 fallback to flat seed direction still works", pts2 == 6 and rq2 is None)

# rising query fed forward into seo_context via deterministic_breakdown
det = svc.deterministic_breakdown(concept, trend, [], date(2026, 7, 13))
check("1-4 rising query pushed into concept seo_context", "capybara coloring page" in (concept.get("seo_context") or []))
check("1-4 demand breakdown records rising_query", det["demand"]["rising_query"] == "capybara coloring page")

# ── 1-5: market query normalization ──
from app.core.search_query import normalize_market_query, head_niche_query
q = normalize_market_query("Woodland Dreams Nursery Animal Print Set Printable Digital Download")
check(f"1-5 strips filler/stopwords: '{q}'", "printable" not in q and "digital" not in q and "download" not in q and "set" not in q)
check("1-5 keeps <=4 content tokens", len(q.split()) <= 4)
check("1-5 keeps the niche words", "nursery" in q and "animal" in q)
check("1-5 head niche is 2 tokens", len(head_niche_query("Woodland Dreams Nursery Animal Print").split()) <= 2)

# _attach_market uses the normalized query + stores it, takes larger head count
from app.agents.trend_research_agent import TrendResearchAgent
agent = TrendResearchAgent.__new__(TrendResearchAgent)

calls = []


class FakeMarket:
    async def validate_concept(self, query):
        calls.append(query)
        # long query -> tiny count; head niche -> big count
        if len(query.split()) <= 2:
            return {"competition_count": 52000, "price_p50": 6.0, "top_titles": []}
        return {"competition_count": 300, "price_p50": 6.0, "top_titles": []}


with patch("app.services.etsy_market_service.EtsyMarketService", FakeMarket):
    data = {"product_name": "Woodland Dreams Nursery Animal Print Set"}
    agent._attach_market(data)

check("1-5 market query is normalized (not the full name)", data["market"]["query"] and "woodland" in data["market"]["query"])
check("1-5 takes the LARGER head-niche competition count", data["market"]["competition_count"] == 52000)
check("1-5 records head_query", data["market"].get("head_query"))
check("1-5 searched both the niche and head queries", len(calls) == 2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-106-B tests passed.")
