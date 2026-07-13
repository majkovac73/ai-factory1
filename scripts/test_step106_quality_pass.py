"""
Step 106-E test — 2-1 seamless enforce, 2-2 wall-art palette regen, 2-3 watermark
formats, 2-4 title guidance, 2-5 name-in-desc relax, 2-7 occasion longest match.

Usage: python scripts/test_step106_quality_pass.py
"""
import os
import sys
import tempfile
from datetime import date
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s106e.db")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 2-3: watermark formats expanded ──
check("2-3 sticker_sheet_design watermarked", "sticker_sheet_design" in settings.WATERMARK_FORMATS)
check("2-3 seamless_pattern watermarked", "seamless_pattern" in settings.WATERMARK_FORMATS)

# ── 2-7: occasion_for prefers the most-specific / earliest match ──
from app.core.seasonality import occasion_for
check("2-7 'christmas gratitude gift' -> christmas (not thanksgiving)",
      occasion_for("Christmas Gratitude Gift Planner", "gratitude and christmas cheer") == "christmas")
check("2-7 plain thanksgiving still maps to thanksgiving",
      occasion_for("Thanksgiving Gratitude Printable", "give thanks") == "thanksgiving")

# ── 2-5: description name check relaxed to >=2 tokens ──
from app.agents.trend_research_agent import TrendResearchAgent
agent = TrendResearchAgent.__new__(TrendResearchAgent)
with patch.object(settings, "PRODUCT_SCORE_ENFORCE", False):
    paraphrased = {"product_name": "Plant Parent Weekly Care Planner", "product_format": "pdf_planner_or_guide",
                   "description": "A weekly plant care planner for houseplant parents.", "page_count": 5}
    err = agent._validate_product(paraphrased)
    check(f"2-5 paraphrased description passes (err={err})", err is None)
    unrelated = {"product_name": "Plant Parent Weekly Care Planner", "product_format": "pdf_planner_or_guide",
                 "description": "A calendar of famous mountains around the world.", "page_count": 5}
    err2 = agent._validate_product(unrelated)
    check("2-5 unrelated description still rejected", err2 is not None)

# ── 2-4: executor title guidance rewritten ──
ex_src = open("app/core/agents/executor.py", encoding="utf-8").read()
check("2-4 title guidance mentions first ~40 chars", "40 char" in ex_src or "first ~40" in ex_src.lower())
check("2-4 title guidance drops the 140-stuffing", "120-140 characters" not in ex_src)

# ── 2-1: seamless enforce in the content-QA loop ──
from app.services.pipeline_orchestrator import PipelineOrchestrator
orch = PipelineOrchestrator()

regen_briefs = []


def fake_pod(task_id, product_name, brief, task_type, report, display_text=None):
    regen_briefs.append(brief)
    return "regenerated_seamless.png"


# edge_mismatch high then low -> one regen then passes
mism_seq = iter([30.0, 12.0])
review = MagicMock()
review.review_asset_file.return_value = MagicMock(passed=True, specific_issues=[])
with patch("app.services.content_quality_service.ContentQualityService", return_value=review), \
     patch("app.core.seamless.edge_mismatch", side_effect=lambda p: next(mism_seq)), \
     patch.object(orch, "_stage_pod_design", side_effect=fake_pod), \
     patch("config.settings.CONTENT_QA_MAX_ATTEMPTS", 3):
    rep = {"stages": {}}
    out = orch._stage_content_quality("t", "seam0.png", "Boho Pattern", "boho tiles", "seamless_pattern", rep)
check("2-1 seamless: one regen when tiling fails then passes", out == "regenerated_seamless.png" and len(regen_briefs) == 1)
check("2-1 seamless regen brief demands perfect tiling", "tile" in regen_briefs[0].lower())

# persistently non-tiling -> blocked
mism_bad = iter([30.0, 30.0])
with patch("app.services.content_quality_service.ContentQualityService", return_value=review), \
     patch("app.core.seamless.edge_mismatch", side_effect=lambda p: next(mism_bad)), \
     patch.object(orch, "_stage_pod_design", side_effect=lambda *a, **k: "still_bad.png"), \
     patch.object(orch, "_block_task") as blk, \
     patch("config.settings.CONTENT_QA_MAX_ATTEMPTS", 2):
    rep2 = {"stages": {}}
    out2 = orch._stage_content_quality("t2", "seam.png", "Boho Pattern", "boho", "seamless_pattern", rep2)
check("2-1 persistently non-tiling pattern is blocked", out2 is None and blk.called)

# ── 2-2: wall-art palette outlier detection ──
# pieces 0,1 close; 2 is the outlier
pairs = [{"a": 0, "b": 1, "distance": 0.05}, {"a": 0, "b": 2, "distance": 0.6}, {"a": 1, "b": 2, "distance": 0.65}]
check("2-2 outlier is the clashing piece (index 2)", PipelineOrchestrator._palette_outlier(pairs, 3) == 2)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-106-E tests passed.")
