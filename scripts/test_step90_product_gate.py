"""
Step 90 test — hard product-before-listing gate + specific concept generation

Context: a real Etsy listing went out reading as "a collection of digital
products and print-on-demand products" with no real deliverable file behind
it. Two root causes are fixed here:

  1. PipelineOrchestrator now treats "a real, validated product artifact
     exists" as a BLOCKING precondition — create_draft_listing() is never
     called if it isn't met, and a listing is deleted + the task is marked
     BLOCKED_NO_PRODUCT if the digital file upload fails after the fact.
  2. TrendResearchAgent now must produce a specific, nameable product_name
     (not a vague market-strategy sentence), with reject-and-retry validation.

Tests:
  [1] digital_download, no delivery asset ever registered (image gen/validation
      failed) -> EtsyClient.create_draft_listing is NEVER called; task is
      marked BLOCKED_NO_PRODUCT.
  [2] digital_download, valid delivery asset registered -> listing IS created.
  [3] pod, PODFulfillmentService.create_product_for_task fails -> no listing
      is created at all (gate blocks before create_draft_listing).
  [4] digital_download, listing created but the digital file upload fails ->
      the listing is deleted (EtsyClient.delete_listing called) and the task
      is blocked.
  [5] TrendResearchAgent._validate_product rejects vague strategy language
      and missing fields; accepts a specific, well-formed product concept.
  [6] TrendResearchAgent.run() retries after a vague first attempt and
      returns the specific product concept produced on retry.

Uses test doubles throughout — no real image generation, no Etsy/Printify API calls.

Usage:
  python scripts/test_step90_product_gate.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test90.db", delete=False)
_tmp.close()
os.environ.pop("DATABASE_PATH", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))

import logging
logging.basicConfig(level=logging.WARNING)

from dotenv import load_dotenv
load_dotenv()

from app.db.database import Base, engine
import app.models.task, app.models.log, app.models.analytics_event
import app.models.image_asset, app.models.marketing_post
Base.metadata.create_all(bind=engine)

# ── step 96: bypass the real vision content-quality gate ──────────────────────
# These pre-96 suites exercise structural gates; replace ContentQualityService
# with an always-pass double so run_post_completion doesn't make real vision
# API calls. The gate's own behaviour is covered in test_step96.
import unittest.mock as _mock96
class _PassCQ96:
    def __init__(self, *a, **k): pass
    def review_asset_file(self, *a, **k): return _mock96.Mock(passed=True, specific_issues=[])
    def review_asset_bytes(self, *a, **k): return _mock96.Mock(passed=True, specific_issues=[])
    def check_marketing_consistency(self, *a, **k): return _mock96.Mock(passed=True, specific_issues=[])
_mock96.patch("app.services.content_quality_service.ContentQualityService", _PassCQ96).start()

from app.services.task_service import TaskService
from app.schemas.task import TaskCreate
from app.schemas.enums import TaskStatus

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 90 — hard product gate + specific concept generation tests\n")

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_done_task(prompt="Celestial Moon Phase Art Print", task_type="single_print"):
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt=prompt, type=task_type))
    ts.update_status(t.id, TaskStatus.PLANNED.value)
    ts.update_status(t.id, TaskStatus.RUNNING.value)
    ts.update_status(t.id, TaskStatus.QA.value)
    ts.save_qa_result(t.id, output_data={
        "title": "Celestial Moon Phase Art Print",
        "description": "A minimalist print featuring the phases of the moon",
        "keywords": ["moon phase", "wall art"],
        "sections": ["Digital Download"],
    }, error_message=None)
    ts.update_status(t.id, TaskStatus.DONE.value)
    return ts.get_task(t.id)


def _fake_image_path(tmp_dir, name="asset.png"):
    from PIL import Image as PILImage
    p = Path(tmp_dir) / name
    img = PILImage.new("RGB", (1024, 1024), color=(200, 150, 100))
    img.save(p, format="PNG")
    return p


from app.services.pipeline_orchestrator import PipelineOrchestrator


# ── [1] no delivery asset -> create_draft_listing NEVER called ────────────────
print("[1] digital_download with no delivery asset: create_draft_listing not called...")

with tempfile.TemporaryDirectory() as tmp:
    hero = _fake_image_path(tmp, "hero.png")
    done1 = _make_done_task()
    orch1 = PipelineOrchestrator()

    etsy_create_calls = []

    class FakeEtsyClient1:
        async def create_draft_listing(self, listing):
            etsy_create_calls.append(listing)
            return {"listing_id": "SHOULD-NOT-HAPPEN"}

    class FailingPODPipelineService1:
        def build_product_record(self, task_id, product_name, visual_brief, product_type):
            # Simulates image generation/validation failure: no design produced.
            return {"task_id": task_id, "design_path": None, "ready_for_pod": False}

    with patch("app.services.pipeline_orchestrator.ProductImageAgent") as m_pia1, \
         patch("app.services.pipeline_orchestrator.PODPipelineService", FailingPODPipelineService1), \
         patch("app.services.pipeline_orchestrator.EtsyClient", FakeEtsyClient1), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga1, \
         patch("app.services.pipeline_orchestrator.PinterestImageService"), \
         patch("app.services.pipeline_orchestrator.MarketingService"):
        m_pia1.return_value.generate_listing_images.return_value = {"hero": hero, "lifestyle": None}
        m_lga1.return_value.generate_listing.return_value = {"title": "t"}
        report1 = orch1.run_post_completion(done1.id)

    task1_after = TaskService().get_task(done1.id)
    blocked_marker = (task1_after.output_data or {}).get("pipeline_status")

    if (
        not etsy_create_calls
        and report1.get("blocked") is True
        and blocked_marker == "BLOCKED_NO_PRODUCT"
    ):
        ok("[1] no delivery asset: EtsyClient.create_draft_listing never called, task BLOCKED_NO_PRODUCT")
    else:
        fail("[1] hard gate on missing delivery asset", f"etsy_calls={etsy_create_calls}, report={report1}, marker={blocked_marker}")


# ── [2] valid delivery asset -> listing IS created ─────────────────────────────
print("[2] digital_download with a valid delivery asset: listing created...")

with tempfile.TemporaryDirectory() as tmp:
    hero2 = _fake_image_path(tmp, "hero2.png")
    design2 = _fake_image_path(tmp, "design2.png")
    done2 = _make_done_task()
    orch2 = PipelineOrchestrator()

    class OkPODPipelineService2:
        def build_product_record(self, task_id, product_name, visual_brief, product_type):
            return {"task_id": task_id, "design_path": str(design2), "ready_for_pod": True}

    with patch("app.services.pipeline_orchestrator.ProductImageAgent") as m_pia2, \
         patch("app.services.pipeline_orchestrator.PODPipelineService", OkPODPipelineService2), \
         patch("app.services.pipeline_orchestrator.EtsyClient") as m_etsy2, \
         patch("app.services.pipeline_orchestrator.EtsyImageService") as m_eis2, \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga2, \
         patch("app.services.pipeline_orchestrator.PinterestImageService") as m_pis2, \
         patch("app.services.pipeline_orchestrator.MarketingService") as m_ms2:
        m_pia2.return_value.generate_listing_images.return_value = {"hero": hero2, "lifestyle": None}
        m_lga2.return_value.generate_listing.return_value = {"title": "t"}
        m_pis2.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(hero2)}
        m_ms2.return_value.get_posts_for_task.return_value = []
        m_ms2.return_value.post_to_channel.return_value = {"success": True}

        _sent2 = {}
        async def _create2(listing):
            _sent2.update(listing)
            return {"listing_id": "L200"}
        m_etsy2.return_value.create_draft_listing.side_effect = _create2

        async def _get_listing2(listing_id):
            return {"listing_id": listing_id, "taxonomy_id": _sent2.get("taxonomy_id"), "when_made": _sent2.get("when_made")}
        m_etsy2.return_value.get_listing.side_effect = _get_listing2

        async def _attach2(**kw):
            return {
                "listing_id": "L200",
                "digital_upload": {"ok": True},
                "uploaded_images": [{"path": "hero2.png", "result": {"ok": True}}],
                "publish_result": {"published": True, "state": "active"},
            }
        m_eis2.return_value.attach_images_and_publish.side_effect = _attach2

        async def _get_images2(listing_id):
            return [{"listing_image_id": 1}]
        m_eis2.return_value.get_listing_images.side_effect = _get_images2

        async def _get_files2(listing_id):
            return [{"listing_file_id": 1, "filetype": "image/png"}]
        m_eis2.return_value.get_listing_files.side_effect = _get_files2

        report2 = orch2.run_post_completion(done2.id)

    cl2 = report2["stages"].get("create_listing", {})
    if cl2.get("ok") is True and not report2.get("blocked"):
        ok("[2] valid delivery asset: listing created normally, task not blocked")
    else:
        fail("[2] listing created with valid asset", f"report={report2}")


# ── [3] POD Printify precheck fails -> no listing created ──────────────────────
print("[3] pod with failing Printify product creation: no listing created...")

with tempfile.TemporaryDirectory() as tmp:
    hero3 = _fake_image_path(tmp, "hero3.png")
    design3 = _fake_image_path(tmp, "design3.png")
    done3 = _make_done_task(task_type="pod_apparel_design")
    orch3 = PipelineOrchestrator()

    class OkPODPipelineService3:
        def build_product_record(self, task_id, product_name, visual_brief, product_type):
            return {"task_id": task_id, "design_path": str(design3), "ready_for_pod": True}

    class FailingPODFulfillmentService3:
        def create_product_for_task(self, task_id, etsy_listing_id=None):
            raise RuntimeError("Printify API error: no print providers available")

    etsy_create_calls3 = []

    class FakeEtsyClient3:
        async def create_draft_listing(self, listing):
            etsy_create_calls3.append(listing)
            return {"listing_id": "SHOULD-NOT-HAPPEN"}

    with patch("app.services.pipeline_orchestrator.ProductImageAgent") as m_pia3, \
         patch("app.services.pipeline_orchestrator.PODPipelineService", OkPODPipelineService3), \
         patch("app.services.pipeline_orchestrator.PODFulfillmentService", FailingPODFulfillmentService3), \
         patch("app.services.pipeline_orchestrator.EtsyClient", FakeEtsyClient3), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga3, \
         patch("app.services.pipeline_orchestrator.PinterestImageService"), \
         patch("app.services.pipeline_orchestrator.MarketingService"):
        m_pia3.return_value.generate_listing_images.return_value = {"hero": hero3, "lifestyle": None}
        m_lga3.return_value.generate_listing.return_value = {"title": "t"}
        report3 = orch3.run_post_completion(done3.id)

    if not etsy_create_calls3 and report3.get("blocked") is True:
        ok("[3] Printify product creation failed: no Etsy listing created, task blocked")
    else:
        fail("[3] pod hard gate", f"etsy_calls={etsy_create_calls3}, report={report3}")


# ── [4] digital upload fails after listing created -> listing deleted + blocked ─
print("[4] digital_download: digital file upload fails after listing created...")

with tempfile.TemporaryDirectory() as tmp:
    hero4 = _fake_image_path(tmp, "hero4.png")
    design4 = _fake_image_path(tmp, "design4.png")
    done4 = _make_done_task()
    orch4 = PipelineOrchestrator()

    class OkPODPipelineService4:
        def build_product_record(self, task_id, product_name, visual_brief, product_type):
            return {"task_id": task_id, "design_path": str(design4), "ready_for_pod": True}

    delete_calls4 = []

    class FakeEtsyClient4:
        def __init__(self):
            self._sent = {}

        async def create_draft_listing(self, listing):
            self._sent = listing
            return {"listing_id": "L400"}

        async def get_listing(self, listing_id):
            return {"listing_id": listing_id, "taxonomy_id": self._sent.get("taxonomy_id"), "when_made": self._sent.get("when_made")}

        async def delete_listing(self, listing_id):
            delete_calls4.append(listing_id)
            return True

    with patch("app.services.pipeline_orchestrator.ProductImageAgent") as m_pia4, \
         patch("app.services.pipeline_orchestrator.PODPipelineService", OkPODPipelineService4), \
         patch("app.services.pipeline_orchestrator.EtsyClient", FakeEtsyClient4), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga4, \
         patch("app.services.pipeline_orchestrator.EtsyImageService") as m_eis4, \
         patch("app.services.pipeline_orchestrator.PinterestImageService"), \
         patch("app.services.pipeline_orchestrator.MarketingService"):
        m_pia4.return_value.generate_listing_images.return_value = {"hero": hero4, "lifestyle": None}
        m_lga4.return_value.generate_listing.return_value = {"title": "t"}

        async def _attach_fail(*a, **kw):
            return {
                "listing_id": "L400",
                "digital_upload": {"error": "Etsy digital file upload error 500: internal error"},
                "uploaded_images": [],
                "publish_result": {"published": False},
            }

        m_eis4.return_value.attach_images_and_publish.side_effect = _attach_fail
        report4 = orch4.run_post_completion(done4.id)

    task4_after = TaskService().get_task(done4.id)
    blocked_marker4 = (task4_after.output_data or {}).get("pipeline_status")

    if (
        delete_calls4 == ["L400"]
        and report4.get("blocked") is True
        and blocked_marker4 == "BLOCKED_NO_PRODUCT"
    ):
        ok("[4] digital upload failure: listing deleted via EtsyClient.delete_listing, task blocked")
    else:
        fail("[4] post-listing digital upload gate", f"delete_calls={delete_calls4}, report={report4}, marker={blocked_marker4}")


# ── [5] TrendResearchAgent concept validation ───────────────────────────────────
print("[5] TrendResearchAgent._validate_product: rejects vague/incomplete concepts...")

from app.agents.trend_research_agent import TrendResearchAgent

with patch("app.agents.trend_research_agent.ResearchAgent"), \
     patch("app.agents.trend_research_agent.IntelligenceAgent"), \
     patch.object(TrendResearchAgent, "__init__", lambda self, *a, **kw: None):
    tra = TrendResearchAgent()
    tra.sanitizer = None

vague = {
    "product_name": "Focus on niche markets catering to specific hobbies",
    "product_format": "single_print",
    "description": "A great strategy for sellers.",
    "target_audience": "everyone",
}
bad_format = {
    "product_name": "Plant Parent Weekly Care Planner",
    "product_format": "poster",
    "description": "The Plant Parent Weekly Care Planner helps you track watering.",
    "target_audience": "plant owners",
}
mismatched_desc = {
    "product_name": "Plant Parent Weekly Care Planner",
    "product_format": "single_print",
    "description": "A great tool for tracking your houseplants.",
    "target_audience": "plant owners",
}
multi_item = {
    "product_name": "Plant Lovers Sticker Bundle Set",
    "product_format": "sticker_sheet_design",
    "description": "The Plant Lovers Sticker Bundle Set includes many stickers.",
    "target_audience": "plant owners",
}
pdf_missing_page_count = {
    "product_name": "Plant Parent Weekly Care Planner",
    "product_format": "pdf_planner_or_guide",
    "description": "The Plant Parent Weekly Care Planner is a printable multi-page tracker.",
    "target_audience": "plant owners",
}
pdf_over_cap = {
    "product_name": "Plant Parent Weekly Care Planner",
    "product_format": "pdf_planner_or_guide",
    "description": "The Plant Parent Weekly Care Planner is a printable multi-page tracker.",
    "target_audience": "plant owners",
    "page_count": 999,
}
valid = {
    "product_name": "Plant Parent Weekly Care Planner",
    "product_format": "single_print",
    "description": "The Plant Parent Weekly Care Planner is a printable tracker for watering schedules.",
    "target_audience": "plant owners",
    "confidence": "high",
}
valid_pdf = {
    "product_name": "Plant Parent Weekly Care Planner",
    "product_format": "pdf_planner_or_guide",
    "description": "The Plant Parent Weekly Care Planner is a printable multi-page tracker.",
    "target_audience": "plant owners",
    "page_count": 4,
    "confidence": "high",
}

results5 = {
    "vague": tra._validate_product(vague),
    "bad_format": tra._validate_product(bad_format),
    "mismatched_desc": tra._validate_product(mismatched_desc),
    "multi_item": tra._validate_product(multi_item),
    "pdf_missing_page_count": tra._validate_product(pdf_missing_page_count),
    "pdf_over_cap": tra._validate_product(pdf_over_cap),
    "valid": tra._validate_product(valid),
    "valid_pdf": tra._validate_product(valid_pdf),
}

if (
    results5["vague"] is not None
    and results5["bad_format"] is not None
    and results5["mismatched_desc"] is not None
    and results5["multi_item"] is not None
    and results5["pdf_missing_page_count"] is not None
    and results5["pdf_over_cap"] is not None
    and results5["valid"] is None
    and results5["valid_pdf"] is None
):
    ok("[5] _validate_product rejects vague/invalid/multi-item/over-cap concepts, accepts specific valid ones")
else:
    fail("[5] _validate_product", f"results={results5}")


# ── [6] TrendResearchAgent.run() retries after a vague first attempt ───────────
print("[6] TrendResearchAgent.run(): retries on vague concept, succeeds on retry...")

gen_calls = []


class FakeResearchAgent6:
    def __init__(self, *a, **kw):
        pass

    def research(self, topic, scope):
        return "Etsy planner market is growing steadily."


class FakeIntelligenceAgent6:
    def __init__(self, *a, **kw):
        pass

    def synthesize(self, research, analysis):
        return {
            "opportunities": ["Focus on niche markets catering to specific hobbies or demographics"],
            "confidence": "medium",
        }


def _fake_generate(self, prompt):
    gen_calls.append(prompt)
    if len(gen_calls) == 1:
        return '{"product_name": "Focus on niche markets", "product_format": "single_print", "description": "strategy", "target_audience": "everyone"}'
    return (
        '{"product_name": "Plant Parent Weekly Care Planner", "product_format": "single_print", '
        '"description": "The Plant Parent Weekly Care Planner is a printable watering tracker.", '
        '"target_audience": "plant owners", "confidence": "high"}'
    )


with patch("app.agents.trend_research_agent.ResearchAgent", FakeResearchAgent6), \
     patch("app.agents.trend_research_agent.IntelligenceAgent", FakeIntelligenceAgent6), \
     patch.object(TrendResearchAgent, "_generate", _fake_generate):
    tra6 = TrendResearchAgent()
    result6 = tra6.run()

if (
    result6
    and result6.get("product_name") == "Plant Parent Weekly Care Planner"
    and len(gen_calls) == 2
):
    ok("[6] run() rejects the vague first attempt and returns the specific retry result")
else:
    fail("[6] run() retry behavior", f"result={result6}, attempts={len(gen_calls)}")


# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
