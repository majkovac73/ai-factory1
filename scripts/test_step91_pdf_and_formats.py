"""
Step 91 test — PDF/multi-image product capability + extended readback gate

Covers:
  [1] Successful single-image product (single_print) -> listing created.
  [2] Successful multi-page PDF product (pdf_planner_or_guide) -> real
      per-page images assembled, independently re-opened via pypdf and
      confirmed to have the expected page count -> listing created.
  [3] Forced PARTIAL PDF failure: 3 pages requested, page 3's image
      generation fails mid-sequence -> NO delivery asset is ever
      registered -> NO listing is created at all (not a listing with
      2/3 pages — the whole task fails).
  [4] Successful multi-image POD product (pod_apparel_design): core design
      submitted to Printify (readback-verified via get_product), plus
      hero+lifestyle listing photos attached to Etsy (readback-verified via
      get_listing_images) -> listing created.
  [5] Forced POD readback failure: Printify's get_product does not show the
      submitted image attached -> create_product_for_task raises -> NO
      listing is created at all.
  [6] Forced Etsy listing-image readback failure: upload appears to
      succeed but GET /listings/{id}/images comes back empty -> the
      already-created listing is deleted and the task is blocked.

Uses test doubles throughout — no real image generation, no Etsy/Printify
API calls. Real Pillow image assembly and real pypdf readback ARE exercised
(no fakes there) since that's the actual capability under test.

Usage:
  python scripts/test_step91_pdf_and_formats.py
"""
import base64
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test91.db", delete=False)
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

from PIL import Image as PILImage

from app.services.task_service import TaskService
from app.schemas.task import TaskCreate
from app.schemas.enums import TaskStatus
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.pdf_generation_service import PDFGenerationService
from app.services.pod_fulfillment_service import PODFulfillmentService

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 91 — PDF generation + multi-image POD + extended readback gate tests\n")

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_done_task(prompt, task_type, sections=None, metadata=None):
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt=prompt, type=task_type, metadata=metadata))
    ts.update_status(t.id, TaskStatus.PLANNED.value)
    ts.update_status(t.id, TaskStatus.RUNNING.value)
    ts.update_status(t.id, TaskStatus.QA.value)
    ts.save_qa_result(t.id, output_data={
        "title": prompt,
        "description": f"A specific product: {prompt}",
        "keywords": ["test"],
        "sections": sections or [],
    }, error_message=None)
    ts.update_status(t.id, TaskStatus.DONE.value)
    return ts.get_task(t.id)


def _fake_image_path(tmp_dir, name="asset.png"):
    p = Path(tmp_dir) / name
    img = PILImage.new("RGB", (1024, 1024), color=(90, 140, 200))
    img.save(p, format="PNG")
    return p


class _FakeImageResult:
    def __init__(self, b64):
        self.b64_data = b64
        self.url = None


class FakeImageProvider:
    """Generates real, valid images — optionally failing after N calls to
    simulate a mid-sequence generation failure."""

    def __init__(self, fail_after: int = None):
        self.fail_after = fail_after
        self.calls = 0

    async def generate_image(self, prompt, **kw):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError(f"simulated image generation failure on call {self.calls}")
        img = PILImage.new("RGB", (800, 1200), color=(50 + self.calls * 10 % 200, 100, 150))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return _FakeImageResult(base64.b64encode(buf.getvalue()).decode())


class OkPODPipelineService:
    def __init__(self, design_path):
        self._design_path = design_path

    def build_product_record(self, task_id, product_name, visual_brief, product_type, **kwargs):
        return {"task_id": task_id, "design_path": str(self._design_path), "ready_for_pod": True}


class FakeEtsyClientHappy:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._sent_by_listing = {}

    async def create_draft_listing(self, listing):
        listing_id = f"L-{len(self.created) + 1}"
        self.created.append(listing_id)
        # Echo back whatever the orchestrator actually sent (taxonomy_id,
        # when_made) — these are "happy path" tests, not mismatch tests.
        self._sent_by_listing[listing_id] = listing
        return {"listing_id": listing_id}

    async def get_listing(self, listing_id):
        sent = self._sent_by_listing.get(listing_id, {})
        return {"listing_id": listing_id, "taxonomy_id": sent.get("taxonomy_id"), "when_made": sent.get("when_made")}

    async def delete_listing(self, listing_id):
        self.deleted.append(listing_id)
        return True


def _make_etsy_image_service_mock(get_images_result=None, get_images_error=None):
    """Builds a MagicMock standing in for EtsyImageService with realistic
    async attach_images_and_publish + get_listing_images behavior."""
    from unittest.mock import MagicMock

    mock_cls = MagicMock()
    _state = {"n": 0}

    async def _attach(listing_id, listing_image_paths, digital_file_path=None, **kwargs):
        uploaded = [{"path": p, "result": {"ok": True}} for p in listing_image_paths]
        _state["n"] = len(uploaded)
        digital_upload = {"ok": True} if digital_file_path else None
        return {
            "listing_id": listing_id,
            "uploaded_images": uploaded,
            "digital_upload": digital_upload,
            "publish_result": {"published": True, "state": "active"},
        }

    async def _get_images(listing_id):
        if get_images_error:
            raise RuntimeError(get_images_error)
        # Echo back as many images as were uploaded (the delivery asset is now
        # prepended as the primary photo for digital single-image products).
        return get_images_result if get_images_result is not None else [{"listing_image_id": i} for i in range(_state["n"])]

    async def _get_files(listing_id):
        return [{"listing_file_id": 1, "filetype": "image/png"}]

    mock_cls.return_value.attach_images_and_publish.side_effect = _attach
    mock_cls.return_value.get_listing_images.side_effect = _get_images
    mock_cls.return_value.get_listing_files.side_effect = _get_files
    return mock_cls


def _patch_common(tmp, extra_etsy_image_kwargs=None):
    hero = _fake_image_path(tmp, "hero.png")
    from unittest.mock import MagicMock
    pia_mock = MagicMock()
    pia_mock.return_value.generate_listing_images.return_value = {"hero": hero, "lifestyle": None}
    lga_mock = MagicMock()
    lga_mock.return_value.generate_listing.return_value = {"title": "t"}
    pis_mock = MagicMock()
    pis_mock.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(hero)}
    ms_mock = MagicMock()
    ms_mock.return_value.get_posts_for_task.return_value = []
    ms_mock.return_value.post_to_channel.return_value = {"success": True}
    return pia_mock, lga_mock, pis_mock, ms_mock


# ── [1] successful single-image product ─────────────────────────────────────
print("[1] single_print: successful single-image product -> listing created...")

with tempfile.TemporaryDirectory() as tmp:
    design1 = _fake_image_path(tmp, "design1.png")
    task1 = _make_done_task("Botanical Line Art Print", "single_print")
    orch1 = PipelineOrchestrator()
    pia1, lga1, pis1, ms1 = _patch_common(tmp)
    etsy1 = FakeEtsyClientHappy()
    eis1 = _make_etsy_image_service_mock()

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia1), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design1)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga1), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy1), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis1), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis1), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms1):
        report1 = orch1.run_post_completion(task1.id)

    if report1["stages"].get("create_listing", {}).get("ok") and not report1.get("blocked") and etsy1.created:
        ok("[1] single-image product created a real listing")
    else:
        fail("[1] single-image product", f"report={report1}")


# ── [2] successful multi-page PDF product ────────────────────────────────────
print("[2] pdf_planner_or_guide: successful 3-page PDF -> listing created...")

with tempfile.TemporaryDirectory() as tmp:
    task2 = _make_done_task(
        "Weekly Meal Prep Planner", "pdf_planner_or_guide",
        sections=["Cover", "Weekly Grid", "Grocery List"],
    )
    orch2 = PipelineOrchestrator()
    pia2, lga2, pis2, ms2 = _patch_common(tmp)
    etsy2 = FakeEtsyClientHappy()
    eis2 = _make_etsy_image_service_mock()
    fake_provider2 = FakeImageProvider()

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia2), \
         patch("app.services.pipeline_orchestrator.PDFGenerationService", lambda: PDFGenerationService(image_provider=fake_provider2)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga2), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy2), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis2), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis2), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms2):
        report2 = orch2.run_post_completion(task2.id)

    delivery2 = report2["stages"].get("delivery_asset", {})
    if (
        delivery2.get("ok") is True
        and delivery2.get("page_count") == 3
        and report2["stages"].get("create_listing", {}).get("ok")
        and not report2.get("blocked")
    ):
        ok("[2] 3-page PDF generated, page-count readback-verified, listing created")
    else:
        fail("[2] successful PDF product", f"report={report2}")


# ── [3] forced PARTIAL PDF failure -> no listing at all ─────────────────────
print("[3] pdf_planner_or_guide: page 3/3 generation fails -> NO listing created...")

with tempfile.TemporaryDirectory() as tmp:
    task3 = _make_done_task(
        "Habit Tracker Journal", "pdf_planner_or_guide",
        sections=["Cover", "Monthly Grid", "Reflection Page"],
    )
    orch3 = PipelineOrchestrator()
    pia3, lga3, pis3, ms3 = _patch_common(tmp)
    etsy3 = FakeEtsyClientHappy()
    eis3 = _make_etsy_image_service_mock()
    fake_provider3 = FakeImageProvider(fail_after=2)  # pages 1-2 succeed, page 3 fails

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia3), \
         patch("app.services.pipeline_orchestrator.PDFGenerationService", lambda: PDFGenerationService(image_provider=fake_provider3)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga3), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy3), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis3), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis3), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms3):
        report3 = orch3.run_post_completion(task3.id)

    delivery3 = report3["stages"].get("delivery_asset", {})
    if (
        delivery3.get("ok") is False
        and report3.get("blocked") is True
        and not etsy3.created
        and report3["stages"].get("create_listing", {}).get("skipped")
    ):
        ok("[3] partial PDF (2/3 pages) never becomes a listing — whole task blocked")
    else:
        fail("[3] partial PDF failure", f"report={report3}, etsy_created={etsy3.created}")


# ── [4] successful multi-image POD product ──────────────────────────────────
print("[4] pod_apparel_design: successful product, Printify + Etsy readback both pass...")

with tempfile.TemporaryDirectory() as tmp:
    design4 = _fake_image_path(tmp, "design4.png")
    task4 = _make_done_task("Retro Sunset Apparel Design", "pod_apparel_design")
    orch4 = PipelineOrchestrator()
    pia4, lga4, pis4, ms4 = _patch_common(tmp)
    etsy4 = FakeEtsyClientHappy()
    eis4 = _make_etsy_image_service_mock()

    from unittest.mock import MagicMock

    class FakeSelectorAgent4:
        log_service = MagicMock()
        def select(self, concept, blueprints):
            return {"blueprint_id": 5}

    class FakePrintifyClient4:
        def upload_image(self, path):
            return "printify-img-4"
        def list_blueprints(self):
            return [{"id": 5, "title": "Tee"}]
        def list_print_providers(self, bp):
            return [{"id": 3, "title": "Provider"}]
        def list_variants(self, bp, pp):
            return {"variants": [{"id": 101, "is_enabled": True}]}
        def create_product(self, **kw):
            return {"id": "printify-product-4"}
        def get_product(self, product_id):
            # Readback CONFIRMS the submitted image is really attached.
            return {"print_areas": [{"placeholders": [{"images": [{"id": "printify-img-4"}]}]}]}

    fake_pod_svc4 = PODFulfillmentService(printify_client=FakePrintifyClient4(), selector_agent=FakeSelectorAgent4())

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia4), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design4)), \
         patch("app.services.pipeline_orchestrator.PODFulfillmentService", lambda *a, **kw: fake_pod_svc4), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga4), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy4), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis4), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis4), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms4):
        report4 = orch4.run_post_completion(task4.id)

    printify4 = report4["stages"].get("printify_product", {})
    if (
        printify4.get("ok") is True
        and printify4.get("etsy_listing_id")
        and report4["stages"].get("create_listing", {}).get("ok")
        and not report4.get("blocked")
    ):
        ok("[4] POD product: Printify readback + Etsy listing-image readback both passed, listing created and linked")
    else:
        fail("[4] successful POD product", f"report={report4}")


# ── [5] forced POD Printify readback failure -> no listing at all ──────────
print("[5] pod_apparel_design: Printify readback shows image NOT attached -> NO listing...")

with tempfile.TemporaryDirectory() as tmp:
    design5 = _fake_image_path(tmp, "design5.png")
    task5 = _make_done_task("Mountain Range Apparel Design", "pod_apparel_design")
    orch5 = PipelineOrchestrator()
    pia5, lga5, pis5, ms5 = _patch_common(tmp)
    etsy5 = FakeEtsyClientHappy()
    eis5 = _make_etsy_image_service_mock()

    from unittest.mock import MagicMock

    class FakeSelectorAgent5:
        log_service = MagicMock()
        def select(self, concept, blueprints):
            return {"blueprint_id": 5}

    class FakePrintifyClient5:
        def upload_image(self, path):
            return "printify-img-5"
        def list_blueprints(self):
            return [{"id": 5, "title": "Tee"}]
        def list_print_providers(self, bp):
            return [{"id": 3, "title": "Provider"}]
        def list_variants(self, bp, pp):
            return {"variants": [{"id": 101, "is_enabled": True}]}
        def create_product(self, **kw):
            return {"id": "printify-product-5"}
        def get_product(self, product_id):
            # Readback shows NO images attached — create_product's response
            # cannot be trusted alone.
            return {"print_areas": [{"placeholders": [{"images": []}]}]}

    fake_pod_svc5 = PODFulfillmentService(printify_client=FakePrintifyClient5(), selector_agent=FakeSelectorAgent5())

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia5), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design5)), \
         patch("app.services.pipeline_orchestrator.PODFulfillmentService", lambda *a, **kw: fake_pod_svc5), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga5), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy5), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis5), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis5), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms5):
        report5 = orch5.run_post_completion(task5.id)

    printify5 = report5["stages"].get("printify_product", {})
    if (
        printify5.get("ok") is False
        and report5.get("blocked") is True
        and not etsy5.created
    ):
        ok("[5] Printify readback failure blocks the task before any listing is created")
    else:
        fail("[5] POD readback failure", f"report={report5}, etsy_created={etsy5.created}")


# ── [6] forced Etsy listing-image readback failure -> listing deleted ──────
print("[6] single_print: Etsy listing-image readback comes back empty -> listing deleted...")

with tempfile.TemporaryDirectory() as tmp:
    design6 = _fake_image_path(tmp, "design6.png")
    task6 = _make_done_task("Ocean Wave Line Art Print", "single_print")
    orch6 = PipelineOrchestrator()
    pia6, lga6, pis6, ms6 = _patch_common(tmp)
    etsy6 = FakeEtsyClientHappy()
    eis6 = _make_etsy_image_service_mock(get_images_result=[])  # upload "succeeds" but readback is empty

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia6), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design6)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga6), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy6), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis6), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis6), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms6):
        report6 = orch6.run_post_completion(task6.id)

    if (
        report6.get("blocked") is True
        and etsy6.deleted == etsy6.created
        and etsy6.created  # a listing WAS created, then rolled back
    ):
        ok("[6] Etsy listing-image readback failure: listing deleted, task blocked")
    else:
        fail("[6] Etsy image readback failure", f"report={report6}, created={etsy6.created}, deleted={etsy6.deleted}")


# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
