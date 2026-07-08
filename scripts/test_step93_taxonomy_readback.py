"""
Step 93 test — specific leaf taxonomy_id at creation + taxonomy readback

Context: Etsy's own listing editor flagged the pipeline's category choice
("Accessories") as too broad. Root cause: nothing upstream of
EtsyClient.create_draft_listing() had ever set taxonomy_id at all, so it
silently defaulted to 1 — Etsy's top-level, most-generic "Accessories"
node — for every listing this system has ever created. Confirmed live
against production (listing 4534427807) and Etsy's real seller-taxonomy
tree (GET /v3/application/seller-taxonomy/nodes).

Fix: app/core/product_formats.py now carries a real, specific LEAF
taxonomy_id per product_format (verified against the live tree, not
guessed). PipelineOrchestrator sends it at creation and independently
reads the listing back afterward to confirm Etsy actually stored the
intended value — the same "generate/create -> independently confirm via
Etsy's own response" pattern as every other readback check this session.

Tests:
  [1] Each recognized product_format's PRODUCT_FORMATS entry carries a
      taxonomy_id that is an int (not the old missing/default-1 shape).
  [2] Orchestrator sends the correct product_format-specific taxonomy_id
      in the create_draft_listing() payload.
  [3] Orchestrator readback: create_draft_listing() "succeeds" but the
      listing's real taxonomy_id (per a fresh GET) doesn't match what was
      requested -> listing deleted, task blocked (same failure pattern as
      every other readback check).

Uses test doubles throughout — no real Etsy API calls.

Usage:
  python scripts/test_step93_taxonomy_readback.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test93.db", delete=False)
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
from app.core.product_formats import PRODUCT_FORMATS

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 93 — specific taxonomy_id at creation + readback tests\n")

# ── [1] every format has a real int taxonomy_id, not the old default-1 shape ──
print("[1] PRODUCT_FORMATS: every format has a specific int taxonomy_id...")

bad = {
    fmt: spec.get("taxonomy_id")
    for fmt, spec in PRODUCT_FORMATS.items()
    if not isinstance(spec.get("taxonomy_id"), int) or spec.get("taxonomy_id") == 1
}
if not bad:
    ok(f"[1] all {len(PRODUCT_FORMATS)} formats have a specific taxonomy_id != 1 (the old 'Accessories' default)")
else:
    fail("[1] taxonomy_id present and specific", f"bad entries: {bad}")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_done_task(prompt, task_type):
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt=prompt, type=task_type))
    ts.update_status(t.id, TaskStatus.PLANNED.value)
    ts.update_status(t.id, TaskStatus.RUNNING.value)
    ts.update_status(t.id, TaskStatus.QA.value)
    ts.save_qa_result(t.id, output_data={
        "title": prompt,
        "description": f"A specific product: {prompt}",
        "keywords": ["test"],
        "sections": [],
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


# ── [2] correct product_format-specific taxonomy_id sent at creation ──────────
print("[2] orchestrator sends the correct product_format-specific taxonomy_id...")

with tempfile.TemporaryDirectory() as tmp:
    design2 = _fake_image_path(tmp, "design2.png")
    task2 = _make_done_task("Just Because Family Recipe Card", "greeting_card_design")
    orch2 = PipelineOrchestrator()
    pia2, lga2, pis2, ms2 = _patch_common(tmp)

    captured_payload = {}

    class RecordingEtsyClient:
        async def create_draft_listing(self, listing):
            captured_payload.update(listing)
            return {"listing_id": "L-rec-1"}

        async def get_listing(self, listing_id):
            return {"listing_id": listing_id, "taxonomy_id": captured_payload.get("taxonomy_id")}

        async def delete_listing(self, listing_id):
            return True

    eis2 = MagicMock()

    async def _attach2(listing_id, listing_image_paths, digital_file_path=None):
        return {
            "listing_id": listing_id,
            "uploaded_images": [{"path": p, "result": {"ok": True}} for p in listing_image_paths],
            "digital_upload": {"ok": True} if digital_file_path else None,
            "publish_result": {"published": True, "state": "active"},
        }

    async def _get_images2(listing_id):
        return [{"listing_image_id": 1}]

    async def _get_files2(listing_id):
        return [{"listing_file_id": 1, "filetype": "image/png"}]

    eis2.return_value.attach_images_and_publish.side_effect = _attach2
    eis2.return_value.get_listing_images.side_effect = _get_images2
    eis2.return_value.get_listing_files.side_effect = _get_files2

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia2), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design2)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga2), \
         patch("app.services.pipeline_orchestrator.EtsyClient", RecordingEtsyClient), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis2), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis2), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms2):
        report2 = orch2.run_post_completion(task2.id)

    expected_taxonomy = PRODUCT_FORMATS["greeting_card_design"]["taxonomy_id"]
    if (
        captured_payload.get("taxonomy_id") == expected_taxonomy
        and expected_taxonomy != 1
        and not report2.get("blocked")
    ):
        ok(f"[2] greeting_card_design listing created with taxonomy_id={expected_taxonomy} (not the old default of 1)")
    else:
        fail("[2] correct taxonomy_id sent", f"captured={captured_payload.get('taxonomy_id')}, expected={expected_taxonomy}, report={report2}")


# ── [3] taxonomy readback mismatch -> listing deleted, task blocked ──────────
print("[3] readback shows a DIFFERENT taxonomy_id than requested -> listing deleted, blocked...")

with tempfile.TemporaryDirectory() as tmp:
    design3 = _fake_image_path(tmp, "design3.png")
    task3 = _make_done_task("Botanical Line Art Print", "single_print")
    orch3 = PipelineOrchestrator()
    pia3, lga3, pis3, ms3 = _patch_common(tmp)

    delete_calls3 = []

    class MismatchEtsyClient:
        async def create_draft_listing(self, listing):
            return {"listing_id": "L-mismatch-1"}

        async def get_listing(self, listing_id):
            # Etsy silently stored/fell back to a different category than requested.
            return {"listing_id": listing_id, "taxonomy_id": 1}

        async def delete_listing(self, listing_id):
            delete_calls3.append(listing_id)
            return True

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia3), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design3)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga3), \
         patch("app.services.pipeline_orchestrator.EtsyClient", MismatchEtsyClient), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis3), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms3):
        report3 = orch3.run_post_completion(task3.id)

    cl3 = report3["stages"].get("create_listing", {})
    if (
        report3.get("blocked") is True
        and "taxonomy_id mismatch" in (report3.get("blocked_reason") or "")
        and delete_calls3 == ["L-mismatch-1"]
        and cl3.get("ok") is False
    ):
        ok("[3] taxonomy readback mismatch: listing deleted, task blocked")
    else:
        fail("[3] taxonomy mismatch gate", f"report={report3}, deletes={delete_calls3}")


# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
