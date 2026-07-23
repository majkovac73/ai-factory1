"""
Traffic/reach features:
  A  themed Pinterest boards  (PinterestBoardService)
  B1 Etsy Ads promote-recommender (AdCandidateService)
  B2 shop-coherence concept bias  (TrendResearchAgent._coherence_block)

Usage: python scripts/test_reach_boards_ads_coherence.py
"""
import os, sys, tempfile, asyncio
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "reach.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ══ A — themed Pinterest boards ═══════════════════════════════════════════════
from app.services.pinterest_board_service import PinterestBoardService

check("A board name: format -> nice board",
      PinterestBoardService.board_name_for({"product_format": "pod_mug", "title": "Random Mug"}) == "Mugs & Drinkware")
# occasion wins over format
name = PinterestBoardService.board_name_for({"product_format": "greeting_card_design",
                                             "title": "Merry Christmas Family Card", "description": "christmas"})
check("A board name: occasion beats format", "Christmas" in name)

async def _fake_list_existing():
    return [{"id": "111", "name": "Mugs & Drinkware", "privacy": "public"}]
async def _fake_list_empty():
    return []
created = {}
async def _fake_create(nm, description=""):
    created["name"] = nm
    return {"id": "999", "name": nm}

# adopts an EXISTING board (no duplicate create)
import app.services.pinterest_board_service as pbs_mod
with patch("app.services.pinterest_oauth.list_boards", _fake_list_existing), \
     patch("app.services.pinterest_oauth.create_board", _fake_create):
    bid = asyncio.new_event_loop().run_until_complete(
        PinterestBoardService().resolve_for({"product_format": "pod_mug", "title": "A Mug"}))
check("A adopts existing board (id 111), no create", bid == "111" and "name" not in created)

# creates a MISSING board + persists
with patch("app.services.pinterest_oauth.list_boards", _fake_list_empty), \
     patch("app.services.pinterest_oauth.create_board", _fake_create):
    bid2 = asyncio.new_event_loop().run_until_complete(
        PinterestBoardService().resolve_for({"product_format": "coloring_page", "title": "Cats"}))
check("A creates missing board (id 999)", bid2 == "999" and created.get("name") == "Coloring Pages")
check("A persists board map", "Coloring Pages" in PinterestBoardService()._load())


# ══ B1 — Etsy Ads promote-recommender ═════════════════════════════════════════
from app.services.ad_candidate_service import AdCandidateService

class TZ:
    from datetime import datetime, timedelta
    def __init__(self, id, type, title, listing=True, occasion=None, age_days=5):
        from datetime import datetime, timedelta
        self.id = id; self.type = type; self.title = title
        self.output_data = {"title": title, "listing_id": ("L" + id) if listing else None,
                            "description": title}
        self.metadata_ = {"occasion": occasion} if occasion else {}
        self.created_at = datetime.utcnow() - timedelta(days=age_days)

class FakeTS:
    def __init__(self, tasks): self.tasks = tasks
    def list_tasks(self): return self.tasks
    def get_task(self, tid): return next((t for t in self.tasks if t.id == tid), None)

class FakePerf:
    def __init__(self, vel): self.vel = vel
    def engagement_velocity(self, tid): return self.vel.get(tid, 0.0)

class FakeRev:
    def profit_by_format(self): return {}

tasks = [
    TZ("hi", "pdf_planner_or_guide", "Budget Planner"),          # higher ticket + will have velocity
    TZ("lo", "coloring_page", "Cat Coloring Page"),              # low ticket, no velocity
    TZ("off", "greeting_card_design", "Christmas Card", occasion="christmas"),  # off-season (July)
    TZ("draft", "single_print", "Unpublished Art", listing=False),  # not published -> excluded
]
with patch("app.services.task_service.TaskService", return_value=FakeTS(tasks)), \
     patch("app.services.performance_service.PerformanceService", return_value=FakePerf({"hi": 8.0})), \
     patch("app.services.revenue_service.RevenueService", return_value=FakeRev()), \
     patch("app.core.seasonality.occasion_for", lambda t, d="": ("christmas" if "christmas" in (t + d).lower() else None)), \
     patch("app.core.seasonality.occasion_in_window", lambda o: False):  # christmas NOT in window (July)
    rec = AdCandidateService().recommend(limit=10)

ids = [c["listing_id"] for c in rec["candidates"]]
check("B1 excludes unpublished listings", "Ldraft" not in ids)
check("B1 top pick is the engaged, higher-ticket listing", rec["candidates"][0]["listing_id"] == "Lhi")
off = next(c for c in rec["candidates"] if c["listing_id"] == "Loff")
top = rec["candidates"][0]
check("B1 off-season listing is de-prioritized", off["promote_score"] < top["promote_score"])
check("B1 candidates carry a plain reason", all(c.get("why") for c in rec["candidates"]))
check("B1 note explains Etsy Ads is manual", "no api" in rec["note"].lower())


# ══ B2 — shop-coherence concept bias ══════════════════════════════════════════
from app.agents.trend_research_agent import TrendResearchAgent
def agent_with(products):
    a = TrendResearchAgent.__new__(TrendResearchAgent)
    a._recent_products = products
    return a

scattered = [(n, "single_print") for n in
             ["Plant Care Journal", "Wedding Seating Chart", "Camping Checklist",
              "Yoga Pose Poster", "Dog Mom Sticker", "Recipe Card Template",
              "Budget Tracker Sheet", "Travel Bucket List"]]  # 8 distinct niches
with patch.object(settings, "SHOP_COHERENCE_ENABLED", True):
    blk = agent_with(scattered)._coherence_block()
check("B2 scattered shop -> coherence block fires", "SHOP COHERENCE" in blk)
check("B2 coherence names niches to deepen", "'plant care'" in blk or "'wedding seating'" in blk)

concentrated = [("Coffee Lover Mug A", "pod_mug"), ("Coffee Lover Mug B", "pod_mug"),
                ("Coffee Lover Print C", "single_print"), ("Wedding Sign One", "single_print"),
                ("Wedding Sign Two", "single_print"), ("Wedding Sign Three", "single_print")]
check("B2 concentrated shop -> no coherence block", agent_with(concentrated)._coherence_block() == "")

with patch.object(settings, "SHOP_COHERENCE_ENABLED", False):
    check("B2 disabled -> no block", agent_with(scattered)._coherence_block() == "")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All reach (boards + ads + coherence) tests passed.")
