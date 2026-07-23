"""
BrainService — the factory's organized, self-consolidating knowledge base.

Usage: python scripts/test_brain.py
"""
import os, sys, tempfile
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "brain.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# create tables in the temp DB
import app.models  # noqa: F401 (registers models)
from app.db.database import engine
from app.models.base import Base
Base.metadata.create_all(engine)

from unittest.mock import patch
from app.services.brain_service import BrainService

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

b = BrainService()

# ── write: lessons UPSERT per subject, timeline kinds APPEND ──────────────────
b.learn("niche", "coffee mugs", "Coffee mugs work.", confidence=0.7)
b.learn("niche", "coffee mugs", "Coffee mugs REALLY work — €40 earned.", confidence=0.9)  # same subject -> update
b.observe("product", "Some Card", "Built a card.")
b.observe("product", "Some Card", "Built another card.")  # observation -> appends
lessons = b.recall(kind="lesson", category="niche")
obs = b.recall(kind="observation", category="product")
check("lesson upserts (one evolving row per subject)", len(lessons) == 1)
check("lesson content is the LATEST", "REALLY work" in lessons[0]["content"])
check("observations append (timeline)", len(obs) == 2)

# ── context_block: only high-confidence lessons, gated when empty ─────────────
fresh = BrainService()
# a brand-new brain (different category filter) still returns the coffee lesson
cb = b.context_block()
check("context block surfaces the learned lesson", "coffee mugs" in cb.lower() and "BRAIN HAS LEARNED" in cb)

low = BrainService()
low.learn("quality", "obscure", "low-confidence note", confidence=0.3)
block = low.context_block()
check("context block excludes low-confidence lessons", "obscure" not in block)

# ── consolidate: distills upstream streams into lessons (no LLM) ──────────────
class FakeNiche:
    def load(self):
        return {"signal_trustworthy": True, "themes": {
            "niche:wedding signs": {"verdict": "winner", "revenue": 24.0, "views": 80, "n_listings": 3, "avg_views": 26.7},
            "niche:fidget toys": {"verdict": "loser", "revenue": 0.0, "views": 2, "n_listings": 4, "avg_views": 0.5},
        }}
class FakeRev:
    def profit_by_format(self):
        return {"pdf_planner_or_guide": {"sales": 2, "net": 18.0, "avg_price": 10.0}}
    def get_total_revenue(self):
        return {"sale_count": 2}
class FakeAn:
    def get_events(self, event_type=None, entity_type=None, entity_id=None, limit=100):
        return []
    def record_event(self, *a, **k): pass
class FakeTS:
    def list_tasks(self): return []

with patch("app.services.niche_memory_service.NicheMemoryService", return_value=FakeNiche()), \
     patch("app.services.revenue_service.RevenueService", return_value=FakeRev()), \
     patch("app.services.analytics_service.AnalyticsService", return_value=FakeAn()), \
     patch("app.services.task_service.TaskService", return_value=FakeTS()):
    res = BrainService().consolidate()

niche_lessons = {l["subject"]: l for l in BrainService().recall(kind="lesson", category="niche")}
fin = BrainService().recall(kind="lesson", category="finance")
mkt = BrainService().recall(kind="lesson", category="market")
check("consolidate learned a WINNER niche", "wedding signs" in niche_lessons and "WORKS" in niche_lessons["wedding signs"]["content"])
check("consolidate learned a DEAD-END niche", "fidget toys" in niche_lessons and "DEAD END" in niche_lessons["fidget toys"]["content"])
check("consolidate learned a finance lesson", any("earns" in l["content"] for l in fin))
check("consolidate learned a market/traffic lesson", any(l["subject"] == "traffic" for l in mkt))

# ── summary: dashboard view ──────────────────────────────────────────────────
s = BrainService().summary()
check("summary counts total knowledge", s["total"] >= 5)
check("summary groups by kind", "lesson" in s["by_kind"])
check("summary surfaces top lessons", len(s["top_lessons"]) >= 1)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All brain tests passed.")
