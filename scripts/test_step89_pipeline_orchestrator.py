"""
Step 89 test — PipelineOrchestrator post-completion hook

Tests:
  [1] run_post_completion() is called when TaskProcessor marks a task DONE
  [2] listing_images stage: digital single-image formats derive listing photos from
      the delivery design (mockups), not independent ProductImageAgent generations
  [3] pod_design stage: called only for pod/digital_download task types
  [4] pinterest stage: calls PinterestImageService + MarketingService
  [5] pinterest stage: skipped if task already posted successfully
  [6] create_listing stage: failure is isolated (attach_publish skipped, not raised)
  [7] listing_images stage: mockup failure is isolated (delivery still the photo, other stages run)

Uses test doubles throughout — no real image generation, no Etsy/Pinterest API calls.

Usage:
  python scripts/test_step89_pipeline_orchestrator.py
"""
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test89.db", delete=False)
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
    global _passed; _passed += 1
    print(f"  [PASS] {label}")

def fail(label, reason):
    global _failed; _failed += 1
    print(f"  [FAIL] {label}: {reason}")

print("\nStep 89 — PipelineOrchestrator tests\n")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_done_task(prompt="Moon phase wall art print", task_type="single_print"):
    """Create a task and push it to DONE with fake output_data."""
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt=prompt, type=task_type))
    ts.update_status(t.id, TaskStatus.PLANNED.value)
    ts.update_status(t.id, TaskStatus.RUNNING.value)
    ts.update_status(t.id, TaskStatus.QA.value)
    ts.save_qa_result(t.id, output_data={
        "title": "Celestial Moon Phase Art Print",
        "description": "A beautiful minimalist print featuring the phases of the moon",
        "keywords": ["moon phase", "wall art", "celestial", "minimalist"],
        "sections": ["Digital Download", "Instant Download"],
    }, error_message=None)
    ts.update_status(t.id, TaskStatus.DONE.value)
    return ts.get_task(t.id)


def _fake_image_path(tmp_dir, name="hero.png"):
    """Write a real 1024x1024 PNG to a temp dir — passes ImageValidationService's
    real dimension/ratio checks (Pillow is a hard dependency, so a 1x1 stub
    would fail the min-size check rather than being skipped)."""
    from PIL import Image as PILImage
    p = Path(tmp_dir) / name
    img = PILImage.new("RGB", (1024, 1024), color=(180, 140, 90))
    img.save(p, format="PNG")
    return p


class _FakePODPipelineServiceDefault:
    """Returns a valid, already-generated delivery design so the hard gate
    passes without hitting a real image provider."""
    def __init__(self, design_path):
        self._design_path = design_path

    def build_product_record(self, task_id, product_name, visual_brief, product_type):
        return {"task_id": task_id, "design_path": str(self._design_path), "ready_for_pod": True}


class _FakeEtsyClientHappy:
    """Real async fake so asyncio.run works — echoes back taxonomy_id for the
    step-93 readback, and returns a listing_id for create."""
    def __init__(self, listing_id="L-89"):
        self._listing_id = listing_id
        self._sent = {}

    async def create_draft_listing(self, listing):
        self._sent[self._listing_id] = listing
        return {"listing_id": self._listing_id}

    async def get_listing(self, listing_id):
        sent = self._sent.get(listing_id, {})
        return {"listing_id": listing_id, "taxonomy_id": sent.get("taxonomy_id"), "when_made": sent.get("when_made")}

    async def delete_listing(self, listing_id):
        return True


def _fake_etsy_image_service_happy():
    """MagicMock EtsyImageService whose async methods all succeed. Stateful:
    the image readback echoes back exactly as many images as attach reported
    uploading (so the step-91 image-count readback passes), and the file
    readback returns a real-MIME file (so the step-92/93b gates pass)."""
    from unittest.mock import MagicMock
    m = MagicMock()
    state = {"n_images": 0}

    async def _attach(listing_id, listing_image_paths, digital_file_path=None):
        uploaded = [{"path": p, "result": {"ok": True}} for p in listing_image_paths]
        state["n_images"] = len(uploaded)
        return {
            "listing_id": listing_id,
            "uploaded_images": uploaded,
            "digital_upload": {"ok": True} if digital_file_path else None,
            "publish_result": {"published": True, "state": "active"},
        }

    async def _get_images(listing_id):
        return [{"listing_image_id": i} for i in range(state["n_images"])]

    async def _get_files(listing_id):
        return [{"listing_file_id": 1, "filetype": "image/png"}]

    m.return_value.attach_images_and_publish.side_effect = _attach
    m.return_value.get_listing_images.side_effect = _get_images
    m.return_value.get_listing_files.side_effect = _get_files
    return m


# ── [1] run_post_completion called after task DONE ────────────────────────────
print("[1] TaskProcessor calls PipelineOrchestrator after DONE...")

from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.task_processor import TaskProcessor

called2 = []

class _FakeOrchestrator(PipelineOrchestrator):
    def run_post_completion(self, task_id):
        called2.append(task_id)
        return {}

proc2 = TaskProcessor()
proc2.pipeline = _FakeOrchestrator()

# Task must start in NEW state — proc2.process() drives it through the lifecycle
ts1 = TaskService()
new_task = ts1.create_task(TaskCreate(prompt="Test hook fires", type="general"))

with patch.object(proc2, "_plan"), \
     patch.object(proc2, "_execute"), \
     patch.object(proc2, "_qa", return_value=True):
    proc2.process(new_task.id)

if called2 and called2[0] == new_task.id:
    ok("[1] TaskProcessor calls pipeline.run_post_completion(task_id) after DONE")
else:
    fail("[1] TaskProcessor pipeline hook", f"called2={called2}, expected [{new_task.id}]")


# ── [2] listing images for a digital single-image format are DERIVED from the ──
#      delivery design (mockups), NOT independently generated (step 100g).
print("[2] listing_images stage: digital single-image -> delivery mockups, not ProductImageAgent...")

with tempfile.TemporaryDirectory() as tmp:
    hero_path = _fake_image_path(tmp, "hero.png")
    lifestyle_path = _fake_image_path(tmp, "lifestyle.png")
    design_path2 = _fake_image_path(tmp, "design2.png")

    done_task2 = _make_done_task()
    orch = PipelineOrchestrator()

    agent_calls = []

    class FakeProductImageAgent:
        def generate_listing_images(self, task_id, product_name, visual_brief, **kw):
            agent_calls.append(task_id)
            return {"hero": hero_path, "lifestyle": lifestyle_path}

    catalog_calls = []
    orig_register = orch.catalog.register
    def _fake_register(**kw):
        catalog_calls.append(kw)
        return orig_register(**kw)
    orch.catalog.register = _fake_register

    report = {}
    with patch("app.services.pipeline_orchestrator.ProductImageAgent", FakeProductImageAgent), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: _FakePODPipelineServiceDefault(design_path2)), \
         patch("app.services.pipeline_orchestrator.asyncio.run", return_value={"listing_id": "FAKE-LID"}), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as mock_lga, \
         patch("app.services.pipeline_orchestrator.EtsyClient"), \
         patch("app.services.pipeline_orchestrator.PinterestImageService") as mock_pis, \
         patch("app.services.pipeline_orchestrator.MarketingService") as mock_ms:
        mock_lga.return_value.generate_listing.return_value = {"title": "test"}
        mock_pis.return_value.enrich_listing_with_image.return_value = {"image_base64": "abc", "pin_image_path": str(hero_path)}
        mock_ms.return_value.get_posts_for_task.return_value = []
        mock_ms.return_value.post_to_channel.return_value = {"success": True}

        report = orch.run_post_completion(done_task2.id)

    img_stage = report["stages"].get("listing_images", {})
    listing_regs = [c for c in catalog_calls if c.get("variant") == "listing"]
    # digital single-image: listing photos come from delivery mockups (source
    # 'delivery_mockup'), ProductImageAgent is NOT called, and the mockups are
    # registered as listing assets.
    if (
        img_stage.get("ok")
        and img_stage.get("source") == "delivery_mockup"
        and not agent_calls
        and len(listing_regs) >= 2
    ):
        ok("[2] listing images derived from delivery (mockups), registered; ProductImageAgent not called")
    else:
        fail("[2] listing_images", f"stage={img_stage}, agent_calls={agent_calls}, listing_regs={len(listing_regs)}")


# ── [3] delivery_asset fires for any recognized format; printify_precheck ──────
#      only fires for the pod category; an unrecognized type is skipped
#      entirely (default-deny gate, step 91).
print("[3] delivery_asset / printify_precheck: conditional on product_format...")

done_unrecognized = _make_done_task(task_type="general")
done_single       = _make_done_task(task_type="single_print")
done_pod          = _make_done_task(task_type="pod_apparel_design")

pod_design_calls = []
printify_precheck_calls = []

class FakePODPipelineService:
    def build_product_record(self, task_id, product_name, visual_brief, product_type):
        pod_design_calls.append(task_id)
        return {"task_id": task_id, "design_path": None, "ready_for_pod": False}

class FakePODFulfillmentService3:
    def create_product_for_task(self, task_id, etsy_listing_id=None):
        printify_precheck_calls.append(task_id)
        raise RuntimeError("no delivery asset in this test double")

with tempfile.TemporaryDirectory() as tmp:
    h = _fake_image_path(tmp, "h.png"); l = _fake_image_path(tmp, "l.png")

    cases = [
        (done_unrecognized, False, False),
        (done_single, True, False),
        (done_pod, True, True),
    ]
    for task, expect_delivery, expect_printify in cases:
        pod_design_calls.clear()
        printify_precheck_calls.clear()
        orch3 = PipelineOrchestrator()
        with patch("app.services.pipeline_orchestrator.ProductImageAgent") as mock_pia, \
             patch("app.services.pipeline_orchestrator.PODPipelineService", FakePODPipelineService), \
             patch("app.services.pipeline_orchestrator.PODFulfillmentService", FakePODFulfillmentService3), \
             patch("app.services.pipeline_orchestrator.asyncio.run", return_value={"listing_id": "L99"}), \
             patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as mock_lga2, \
             patch("app.services.pipeline_orchestrator.EtsyClient"), \
             patch("app.services.pipeline_orchestrator.PinterestImageService") as mock_pis3, \
             patch("app.services.pipeline_orchestrator.MarketingService") as mock_ms3:
            mock_pia.return_value.generate_listing_images.return_value = {"hero": h, "lifestyle": l}
            mock_lga2.return_value.generate_listing.return_value = {"title": "t"}
            mock_pis3.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(h)}
            mock_ms3.return_value.get_posts_for_task.return_value = []
            mock_ms3.return_value.post_to_channel.return_value = {"success": True}
            orch3.run_post_completion(task.id)

        delivery_fired = bool(pod_design_calls)
        printify_fired = bool(printify_precheck_calls)
        if delivery_fired == expect_delivery and printify_fired == expect_printify:
            ok(f"[3] type='{task.type}': delivery_asset={delivery_fired}, printify_precheck={printify_fired}")
        else:
            fail(
                f"[3] type='{task.type}'",
                f"delivery_asset={delivery_fired} (expected {expect_delivery}), "
                f"printify_precheck={printify_fired} (expected {expect_printify})",
            )


# ── [4] pinterest stage: SocialImageAgent + MarketingService called ────────────
print("[4] pinterest stage: image generated and post attempted...")

with tempfile.TemporaryDirectory() as tmp:
    h4 = _fake_image_path(tmp, "h4.png"); l4 = _fake_image_path(tmp, "l4.png")
    pin4 = _fake_image_path(tmp, "pin4.png")
    design4 = _fake_image_path(tmp, "design4.png")

    done4 = _make_done_task()
    orch4 = PipelineOrchestrator()
    post_calls = []

    class FakeMarketingService4:
        def get_posts_for_task(self, tid): return []
        def post_to_channel(self, task_id, listing, channel):
            post_calls.append({"task_id": task_id, "has_image": "image_base64" in listing})
            return {"success": True}

    class FakePinterestImageService4:
        def enrich_listing_with_image(self, listing, task_id, visual_brief):
            return {**listing, "image_base64": "FAKEBASE64", "pin_image_path": str(pin4)}

    with patch("app.services.pipeline_orchestrator.ProductImageAgent") as m_pia4, \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: _FakePODPipelineServiceDefault(design4)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga4, \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=_FakeEtsyClientHappy("L44")), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", _fake_etsy_image_service_happy()), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", FakePinterestImageService4), \
         patch("app.services.pipeline_orchestrator.MarketingService", FakeMarketingService4), \
         patch("app.services.pipeline_orchestrator.PinterestChannel"):
        m_pia4.return_value.generate_listing_images.return_value = {"hero": h4, "lifestyle": l4}
        m_lga4.return_value.generate_listing.return_value = {"title": "t"}
        orch4.run_post_completion(done4.id)

    if post_calls and post_calls[0]["has_image"]:
        ok("[4] pinterest stage: image enriched and post_to_channel called with image_base64")
    else:
        fail("[4] pinterest stage", f"post_calls={post_calls}")


# ── [5] pinterest stage: idempotency — skipped if already posted ──────────────
print("[5] pinterest stage: skipped if already successfully posted...")

with tempfile.TemporaryDirectory() as tmp:
    h5 = _fake_image_path(tmp, "h5.png"); l5 = _fake_image_path(tmp, "l5.png")
    design5 = _fake_image_path(tmp, "design5.png")
    done5 = _make_done_task()
    orch5 = PipelineOrchestrator()
    post_calls5 = []

    class FakeMarketingService5:
        def get_posts_for_task(self, tid):
            existing = MagicMock()
            existing.channel = "pinterest"
            existing.status = "success"
            return [existing]
        def post_to_channel(self, *a, **kw):
            post_calls5.append(True)
            return {"success": True}

    with patch("app.services.pipeline_orchestrator.ProductImageAgent") as m_pia5, \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: _FakePODPipelineServiceDefault(design5)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga5, \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=_FakeEtsyClientHappy("L55")), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", _fake_etsy_image_service_happy()), \
         patch("app.services.pipeline_orchestrator.PinterestImageService"), \
         patch("app.services.pipeline_orchestrator.MarketingService", FakeMarketingService5):
        m_pia5.return_value.generate_listing_images.return_value = {"hero": h5, "lifestyle": l5}
        m_lga5.return_value.generate_listing.return_value = {"title": "t"}
        report5 = orch5.run_post_completion(done5.id)

    stage5 = report5["stages"].get("pinterest", {})
    if not post_calls5 and "skipped" in stage5:
        ok("[5] pinterest skipped when already posted successfully")
    else:
        fail("[5] pinterest idempotency", f"post_calls5={post_calls5}, stage={stage5}")


# ── [6] create_listing failure: attach_publish skipped, other stages run ───────
print("[6] create_listing failure: attach_publish skipped, pinterest still runs...")

with tempfile.TemporaryDirectory() as tmp:
    h6 = _fake_image_path(tmp, "h6.png"); l6 = _fake_image_path(tmp, "l6.png")
    pin6 = _fake_image_path(tmp, "pin6.png")
    design6 = _fake_image_path(tmp, "design6.png")
    done6 = _make_done_task()
    orch6 = PipelineOrchestrator()
    pin_calls6 = []

    def _raise_etsy(*a, **kw):
        raise RuntimeError("Etsy API key not configured")

    class FakePinterestImageService6:
        def enrich_listing_with_image(self, listing, task_id, visual_brief):
            return {**listing, "image_base64": "x", "pin_image_path": str(pin6)}

    class FakeMarketingService6:
        def get_posts_for_task(self, tid): return []
        def post_to_channel(self, task_id, listing, channel):
            pin_calls6.append(True); return {"success": True}

    with patch("app.services.pipeline_orchestrator.ProductImageAgent") as m_pia6, \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: _FakePODPipelineServiceDefault(design6)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga6, \
         patch("app.services.pipeline_orchestrator.asyncio.run", side_effect=_raise_etsy), \
         patch("app.services.pipeline_orchestrator.EtsyClient"), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", FakePinterestImageService6), \
         patch("app.services.pipeline_orchestrator.MarketingService", FakeMarketingService6), \
         patch("app.services.pipeline_orchestrator.PinterestChannel"):
        m_pia6.return_value.generate_listing_images.return_value = {"hero": h6, "lifestyle": l6}
        m_lga6.return_value.generate_listing.return_value = {"title": "t"}
        report6 = orch6.run_post_completion(done6.id)

    cl6 = report6["stages"].get("create_listing", {})
    ap6 = report6["stages"].get("attach_publish", {})
    pin6_stage = report6["stages"].get("pinterest", {})

    if cl6.get("ok") is False and "skipped" in ap6 and pin_calls6:
        ok("[6] create_listing failure: attach_publish skipped, pinterest still ran")
    else:
        fail("[6] isolated failure", f"create={cl6}, attach={ap6}, pinterest={pin6_stage}")


# ── [7] listing-image (mockup) failure is isolated: the delivery design still ──
#      stands as the listing photo and downstream stages still run (step 100g).
print("[7] listing mockup failure isolated: delivery still the photo, downstream stages run...")

with tempfile.TemporaryDirectory() as tmp:
    pin7 = _fake_image_path(tmp, "pin7.png")
    design7 = _fake_image_path(tmp, "design7.png")
    done7 = _make_done_task()  # single_print (digital single-image)
    orch7 = PipelineOrchestrator()
    pin_calls7 = []

    # Simulate mockup generation failing entirely (returns none). The prepended
    # delivery design must still stand as the listing photo and the pipeline
    # must continue.
    def _failing_mockups(task_id, delivery_path, report):
        report["stages"]["listing_images"] = {"ok": False, "count": 0, "source": "delivery_mockup"}
        return []
    orch7._build_listing_mockups = _failing_mockups

    class FakePinterestImageService7:
        def enrich_listing_with_image(self, listing, task_id, visual_brief):
            return {**listing, "image_base64": "x", "pin_image_path": str(pin7)}

    class FakeMarketingService7:
        def get_posts_for_task(self, tid): return []
        def post_to_channel(self, task_id, listing, channel):
            pin_calls7.append(True); return {"success": True}

    with patch("app.services.pipeline_orchestrator.ProductImageAgent"), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: _FakePODPipelineServiceDefault(design7)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as m_lga7, \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=_FakeEtsyClientHappy("L77")), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", _fake_etsy_image_service_happy()), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", FakePinterestImageService7), \
         patch("app.services.pipeline_orchestrator.MarketingService", FakeMarketingService7), \
         patch("app.services.pipeline_orchestrator.PinterestChannel"):
        m_lga7.return_value.generate_listing.return_value = {"title": "t"}
        report7 = orch7.run_post_completion(done7.id)

    img7 = report7["stages"].get("listing_images", {})
    listing7 = report7["stages"].get("create_listing", {})

    if img7.get("ok") is False and listing7.get("ok") and pin_calls7:
        ok("[7] mockup failure isolated: listing still created from the delivery design, pinterest ran")
    else:
        fail("[7] isolated mockup failure", f"img={img7}, listing={listing7}, pin={pin_calls7}")


# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
