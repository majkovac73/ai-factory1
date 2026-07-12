"""
Step 105-E test — 2-1 winner-variant gates, 2-2 seamless_pattern advertised,
2-3 AnalysisAgent removed, 2-4 research topic reflects proposable formats.

Usage: python scripts/test_step105_concept_path.py
"""
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s105e.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 2-1: winner-variant skips an out-of-window occasion ──
import app.workers.etsy_receipt_worker as erw
from app.services.task_service import TaskService as RealTaskService
_real_get_task = RealTaskService().get_task  # bind BEFORE patching TaskService
worker = erw.EtsyReceiptWorker(fulfillment_service=MagicMock())

db = SessionLocal()
db.add(Task(id="hween", prompt="p", type="coloring_page", status="DONE", input_data={},
            output_data={"title": "Halloween Ghost Coloring Page", "description": "spooky ghosts"},
            metadata_={"occasion": "halloween"}))
db.add(Task(id="evergreen", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={"title": "Minimalist Mountain Range Print", "description": "calm mountains"},
            metadata_={}))
db.commit()
db.close()

with patch.object(settings, "AUTONOMY_ENABLED", True), patch.object(settings, "WINNER_VARIANTS_PER_DAY", 2):
    auto = MagicMock()
    auto.can_create_winner_variant.return_value = True
    auto.can_spend.return_value = True
    # today is July 12 (from the harness) — Halloween is far out of window
    with patch("app.services.autonomy_service.AutonomyService", return_value=auto), \
         patch("app.services.task_service.TaskService") as TS:
        # real task lookup for the parent
        TS.return_value.get_task.side_effect = _real_get_task
        worker._maybe_spawn_winner_variant("hween")
        halloween_created = TS.return_value.create_task.called
    check("2-1 out-of-window Halloween parent spawns NO variant", not halloween_created)

with patch.object(settings, "AUTONOMY_ENABLED", True), patch.object(settings, "WINNER_VARIANTS_PER_DAY", 2):
    auto2 = MagicMock()
    auto2.can_create_winner_variant.return_value = True
    auto2.can_spend.return_value = True
    with patch("app.services.autonomy_service.AutonomyService", return_value=auto2), \
         patch("app.services.task_service.TaskService") as TS2:
        TS2.return_value.get_task.side_effect = _real_get_task
        TS2.return_value.create_task.return_value = MagicMock(id="v1")
        worker._maybe_spawn_winner_variant("evergreen")
        evergreen_created = TS2.return_value.create_task.called
    check("2-1 evergreen parent DOES spawn a variant", evergreen_created)

# ── 2-2: seamless_pattern advertised in the concept prompt ──
from app.agents.trend_research_agent import TrendResearchAgent, _RESEARCH_TOPIC_DIGITAL, _RESEARCH_TOPIC_WITH_POD
agent = TrendResearchAgent.__new__(TrendResearchAgent)
agent._recent_products = []
agent._insights_block = ""
prompt = agent._build_concept_prompt("some insight", "")
check("2-2 seamless_pattern is in the format menu", "seamless_pattern" in prompt and "tileable" in prompt.lower())

# ── 2-3: AnalysisAgent removed; synthesize takes research only ──
import inspect
from app.agents.market_intelligence.intelligence import IntelligenceAgent
sig = inspect.signature(IntelligenceAgent.synthesize)
check("2-3 synthesize() drops the analysis param", list(sig.parameters) == ["self", "research"])
try:
    import app.agents.market_intelligence.analysis  # noqa
    check("2-3 analysis module deleted", False)
except ModuleNotFoundError:
    check("2-3 analysis module deleted", True)
import app.agents.market_intelligence as mi
check("2-3 AnalysisAgent not exported", "AnalysisAgent" not in getattr(mi, "__all__", []))
# registry still builds cleanly without the analysis agent
from app.agents.registry import list_agents
agents = list_agents()
check("2-3 registry builds without analysis", "analysis" not in agents and "intelligence" in agents)

# ── 2-4: research topic reflects POD state ──
with patch.object(settings, "POD_APPAREL_ENABLED", False):
    topic = (_RESEARCH_TOPIC_WITH_POD if getattr(settings, "POD_APPAREL_ENABLED", False) else _RESEARCH_TOPIC_DIGITAL)
check("2-4 POD-off topic has no 'print-on-demand'", "print-on-demand" not in _RESEARCH_TOPIC_DIGITAL)
check("2-4 POD-on topic mentions print-on-demand", "print-on-demand" in _RESEARCH_TOPIC_WITH_POD)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-105-E tests passed.")
