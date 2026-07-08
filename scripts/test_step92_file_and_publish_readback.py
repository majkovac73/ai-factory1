"""
Step 92 test — digital-file readback + publish-state readback

Context: a real listing (task fb66a81a, listing 4534427807, "Customizable
Family Recipe Greeting Card") went live with 2 real listing photos but
Maj found no downloadable file. Investigation via railway ssh + real Etsy
API calls found:

  - The digital file WAS genuinely attached from the very first pipeline
    run (Etsy's own getAllListingFiles: count=1, design.png, 550KB) — the
    ticket's assumed root cause (file never uploaded) was wrong.
  - The REAL root cause: EtsyImageService.publish_listing()'s PATCH call
    returned HTTP 200, but the listing's own `state` field in that same
    response body stayed "edit" (draft, invisible to buyers) — Etsy
    accepted the request without erroring but the activation didn't take
    effect, almost certainly a brief propagation lag right after the
    image/file uploads that happen in the same call sequence just before
    it. A manual re-invocation of the identical PATCH, done later during
    this investigation, DID transition it to "active" — confirming the
    underlying data was fine, just not yet consistent at the moment of
    the original attempt. publish_listing() only checked the HTTP status
    code, never the response body's actual `state` — the same class of
    "trust the status code, not the truth" bug as before.
  - Separately, and correctly anticipated by this ticket even though it
    wasn't the actual cause this time: there was no independent readback
    confirming the digital FILE's presence at all (only images had one).
    That gap is now closed too, since it's exactly the kind of blind spot
    that could cause the literal bug Maj described in a different
    scenario (e.g. a wrong field/content-type silently not attaching a
    file while still returning 201).

Tests:
  [1] EtsyImageService.publish_listing(): PATCH returns 200 but body state
      stays "edit" on the first attempt, "active" on retry -> succeeds
      after one retry, published=True (proves the actual production fix).
  [2] EtsyImageService.publish_listing(): PATCH returns 200 with state
      stuck at "edit" on BOTH attempts -> published=False (proves it does
      NOT blindly trust the HTTP status code when retries are exhausted).
  [3] PipelineOrchestrator: digital file upload call succeeds (200-level
      response, no "error" key) but get_listing_files() readback reports
      zero files attached -> listing deleted, task blocked
      (the ticket's explicit ask).
  [4] PipelineOrchestrator: attach/publish succeeds and image readback
      passes, but publish_result reports published=False (state never
      became "active") -> listing deleted, task blocked (the actual
      production bug, reproduced end-to-end through the orchestrator).

Uses test doubles throughout — no real Etsy API calls.

Usage:
  python scripts/test_step92_file_and_publish_readback.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test92.db", delete=False)
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

from PIL import Image as PILImage

from app.services.task_service import TaskService
from app.schemas.task import TaskCreate
from app.schemas.enums import TaskStatus
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.etsy_image_service import EtsyImageService

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 92 — digital-file readback + publish-state readback tests\n")

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_done_task(prompt, task_type, sections=None):
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt=prompt, type=task_type))
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


class OkPODPipelineService:
    def __init__(self, design_path):
        self._design_path = design_path

    def build_product_record(self, task_id, product_name, visual_brief, product_type):
        return {"task_id": task_id, "design_path": str(self._design_path), "ready_for_pod": True}


class FakeEtsyClientHappy:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._taxonomy_by_listing = {}

    async def create_draft_listing(self, listing):
        listing_id = f"L-{len(self.created) + 1}"
        self.created.append(listing_id)
        self._taxonomy_by_listing[listing_id] = listing.get("taxonomy_id")
        return {"listing_id": listing_id}

    async def get_listing(self, listing_id):
        return {"listing_id": listing_id, "taxonomy_id": self._taxonomy_by_listing.get(listing_id)}

    async def delete_listing(self, listing_id):
        self.deleted.append(listing_id)
        return True


def _patch_common(tmp):
    hero = _fake_image_path(tmp, "hero.png")
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


# ── [1] publish_listing(): retries once, succeeds on retry ──────────────────
print("[1] EtsyImageService.publish_listing(): 'edit' then 'active' on retry -> succeeds...")

import asyncio


async def _run_test_1():
    svc = EtsyImageService()
    call_count = {"n": 0}

    async def fake_patch(self_ignored, listing_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"listing_id": listing_id, "state": "edit"}
        return {"listing_id": listing_id, "state": "active"}

    with patch("app.services.etsy_image_service.settings.AUTO_PUBLISH_LISTINGS", True), \
         patch.object(EtsyImageService, "_patch_listing_state_active", fake_patch), \
         patch("app.services.etsy_image_service.asyncio.sleep", return_value=None):
        result = await svc.publish_listing("L1")
    return result, call_count["n"]


result1, calls1 = asyncio.run(_run_test_1())
if result1.get("published") is True and result1.get("state") == "active" and calls1 == 2:
    ok("[1] publish_listing() retries once and reports published=True when the retry succeeds")
else:
    fail("[1] publish_listing retry-success", f"result={result1}, calls={calls1}")


# ── [2] publish_listing(): stuck on 'edit' even after retry -> published=False ──
print("[2] EtsyImageService.publish_listing(): stuck on 'edit' after retry -> published=False...")


async def _run_test_2():
    svc = EtsyImageService()
    call_count = {"n": 0}

    async def fake_patch(self_ignored, listing_id):
        call_count["n"] += 1
        return {"listing_id": listing_id, "state": "edit"}

    with patch("app.services.etsy_image_service.settings.AUTO_PUBLISH_LISTINGS", True), \
         patch.object(EtsyImageService, "_patch_listing_state_active", fake_patch), \
         patch("app.services.etsy_image_service.asyncio.sleep", return_value=None):
        result = await svc.publish_listing("L2")
    return result, call_count["n"]


result2, calls2 = asyncio.run(_run_test_2())
if result2.get("published") is False and result2.get("state") == "edit" and calls2 == 2:
    ok("[2] publish_listing() does NOT report success when state never becomes 'active' (tried twice)")
else:
    fail("[2] publish_listing persistent failure", f"result={result2}, calls={calls2}")


# ── [3] orchestrator: file upload 'succeeds' but readback shows 0 files ────────
print("[3] orchestrator: digital file upload call succeeds but file readback is empty -> blocked...")

with tempfile.TemporaryDirectory() as tmp:
    design3 = _fake_image_path(tmp, "design3.png")
    task3 = _make_done_task("Botanical Line Art Print", "single_print")
    orch3 = PipelineOrchestrator()
    pia3, lga3, pis3, ms3 = _patch_common(tmp)
    etsy3 = FakeEtsyClientHappy()

    eis3 = MagicMock()

    async def _attach3(listing_id, listing_image_paths, digital_file_path=None):
        return {
            "listing_id": listing_id,
            "uploaded_images": [{"path": p, "result": {"ok": True}} for p in listing_image_paths],
            "digital_upload": {"ok": True, "listing_file_id": 999},  # HTTP-level "success"
            "publish_result": {"published": True, "state": "active"},
        }

    async def _get_images3(listing_id):
        return [{"listing_image_id": 1}]

    async def _get_files3(listing_id):
        return []  # ground truth: nothing is actually there

    eis3.return_value.attach_images_and_publish.side_effect = _attach3
    eis3.return_value.get_listing_images.side_effect = _get_images3
    eis3.return_value.get_listing_files.side_effect = _get_files3

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia3), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design3)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga3), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy3), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis3), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis3), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms3):
        report3 = orch3.run_post_completion(task3.id)

    if (
        report3.get("blocked") is True
        and "file readback failed" in (report3.get("blocked_reason") or "")
        and etsy3.deleted == etsy3.created
        and etsy3.created
    ):
        ok("[3] file-upload 'success' with empty readback -> listing deleted, task blocked")
    else:
        fail("[3] file readback failure", f"report={report3}, created={etsy3.created}, deleted={etsy3.deleted}")


# ── [4] orchestrator: publish never actually took effect -> blocked ──────────
print("[4] orchestrator: publish_result reports published=False -> listing deleted, blocked...")

with tempfile.TemporaryDirectory() as tmp:
    design4 = _fake_image_path(tmp, "design4.png")
    task4 = _make_done_task("Ocean Wave Line Art Print", "single_print")
    orch4 = PipelineOrchestrator()
    pia4, lga4, pis4, ms4 = _patch_common(tmp)
    etsy4 = FakeEtsyClientHappy()

    eis4 = MagicMock()

    async def _attach4(listing_id, listing_image_paths, digital_file_path=None):
        return {
            "listing_id": listing_id,
            "uploaded_images": [{"path": p, "result": {"ok": True}} for p in listing_image_paths],
            "digital_upload": {"ok": True, "listing_file_id": 1},
            # Ground truth reproduced from production: PATCH returned 200
            # both times, but state never actually became "active".
            "publish_result": {"published": False, "state": "edit"},
        }

    async def _get_images4(listing_id):
        return [{"listing_image_id": 1}]

    async def _get_files4(listing_id):
        return [{"listing_file_id": 1, "filetype": "image/png"}]

    eis4.return_value.attach_images_and_publish.side_effect = _attach4
    eis4.return_value.get_listing_images.side_effect = _get_images4
    eis4.return_value.get_listing_files.side_effect = _get_files4

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia4), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design4)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga4), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy4), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis4), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis4), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms4):
        report4 = orch4.run_post_completion(task4.id)

    if (
        report4.get("blocked") is True
        and "publish did not take effect" in (report4.get("blocked_reason") or "")
        and etsy4.deleted == etsy4.created
        and etsy4.created
    ):
        ok("[4] publish never actually activated -> listing deleted, task blocked (reproduces the real production bug)")
    else:
        fail("[4] publish-state failure", f"report={report4}, created={etsy4.created}, deleted={etsy4.deleted}")


# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
