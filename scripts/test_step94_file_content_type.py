"""
Step 94 test — digital file uploaded with correct MIME type + editor-display gate

Context: listing 4534427807's digital file WAS attached (getAllListingFiles
count=1) but never appeared in Etsy's listing editor. Root cause, confirmed
by live field-by-field comparison against a manually-made listing whose file
DID display: our file's stored `filetype` was `application/octet-stream`
(an unrecognised generic binary), because upload_digital_file() hardcoded
that content-type for every upload. Etsy stores exactly the multipart
content-type we send, and its editor only renders files with a recognised
type. A hard refresh did NOT surface it (cache ruled out by Maj), and the
taxonomy PATCH did NOT destroy the file (still count=1 after) — so it was
purely a content-type bug.

Tests:
  [1] _guess_content_type(): .png -> image/png, .pdf -> application/pdf,
      unknown extension -> application/octet-stream (fallback only).
  [2] EtsyImageService.upload_digital_file() sends the file's REAL MIME
      type in the multipart content-type, not a hardcoded octet-stream.
  [3] Orchestrator readback: a file attached but stored as
      application/octet-stream (present, count>=1, but won't display) is
      treated as a readback FAILURE -> listing deleted, task blocked
      (this specific check would have caught the real bug, which the
      step-92 "count >= 1" check passed straight through).
  [4] Orchestrator readback: a file with a real MIME type (image/png)
      passes the gate normally.

Uses test doubles throughout — no real Etsy API calls.

Usage:
  python scripts/test_step94_file_content_type.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test94.db", delete=False)
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
from app.services.etsy_image_service import EtsyImageService, _guess_content_type, GENERIC_BINARY_CONTENT_TYPE

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 94 — digital file content-type + editor-display readback tests\n")


# ── [1] _guess_content_type ─────────────────────────────────────────────────
print("[1] _guess_content_type(): real MIME per extension, octet-stream only as fallback...")

r_png = _guess_content_type("/data/x/design.png", "design.png")
r_pdf = _guess_content_type("/data/x/design.pdf", "design.pdf")
r_unknown = _guess_content_type("/data/x/blob", "blob")

if r_png == "image/png" and r_pdf == "application/pdf" and r_unknown == GENERIC_BINARY_CONTENT_TYPE:
    ok("[1] .png -> image/png, .pdf -> application/pdf, unknown -> octet-stream fallback")
else:
    fail("[1] _guess_content_type", f"png={r_png}, pdf={r_pdf}, unknown={r_unknown}")


# ── [2] upload_digital_file sends the real MIME type ────────────────────────
print("[2] upload_digital_file() sends the file's real MIME type, not hardcoded octet-stream...")

import asyncio


async def _run_test_2():
    tmp_dir = tempfile.mkdtemp()
    png_path = Path(tmp_dir) / "design.png"
    PILImage.new("RGB", (64, 64), color=(1, 2, 3)).save(png_path, format="PNG")

    captured = {}

    class _CaptureResponse:
        status_code = 201
        def json(self):
            return {"listing_file_id": 123, "filetype": "image/png"}

    class _CaptureClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, files=None, data=None):
            # files["file"] == (filename, bytes, content_type)
            captured["content_type"] = files["file"][2]
            captured["filename"] = files["file"][0]
            return _CaptureResponse()

    svc = EtsyImageService()
    with patch("app.services.etsy_image_service.get_valid_access_token", return_value="tok"), \
         patch("app.services.etsy_image_service.httpx.AsyncClient", _CaptureClient):
        await svc.upload_digital_file("L1", str(png_path))
    return captured


captured2 = asyncio.run(_run_test_2())
if captured2.get("content_type") == "image/png":
    ok("[2] upload_digital_file() sent content-type image/png for a .png file")
else:
    fail("[2] upload content-type", f"sent content_type={captured2.get('content_type')!r}")


# ── Helpers for orchestrator tests ──────────────────────────────────────────


def _make_done_task(prompt, task_type):
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt=prompt, type=task_type))
    ts.update_status(t.id, TaskStatus.PLANNED.value)
    ts.update_status(t.id, TaskStatus.RUNNING.value)
    ts.update_status(t.id, TaskStatus.QA.value)
    ts.save_qa_result(t.id, output_data={
        "title": prompt, "description": f"A specific product: {prompt}",
        "keywords": ["test"], "sections": [],
    }, error_message=None)
    ts.update_status(t.id, TaskStatus.DONE.value)
    return ts.get_task(t.id)


def _fake_image_path(tmp_dir, name="asset.png"):
    p = Path(tmp_dir) / name
    PILImage.new("RGB", (1024, 1024), color=(90, 140, 200)).save(p, format="PNG")
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
        self._sent = {}
    async def create_draft_listing(self, listing):
        lid = f"L-{len(self.created) + 1}"
        self.created.append(lid)
        self._sent[lid] = listing
        return {"listing_id": lid}
    async def get_listing(self, listing_id):
        sent = self._sent.get(listing_id, {})
        return {"listing_id": listing_id, "taxonomy_id": sent.get("taxonomy_id"), "when_made": sent.get("when_made")}
    async def delete_listing(self, listing_id):
        self.deleted.append(listing_id)
        return True


def _patch_common(tmp):
    hero = _fake_image_path(tmp, "hero.png")
    pia = MagicMock(); pia.return_value.generate_listing_images.return_value = {"hero": hero, "lifestyle": None}
    lga = MagicMock(); lga.return_value.generate_listing.return_value = {"title": "t"}
    pis = MagicMock(); pis.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(hero)}
    ms = MagicMock(); ms.return_value.get_posts_for_task.return_value = []; ms.return_value.post_to_channel.return_value = {"success": True}
    return pia, lga, pis, ms


def _run_with_filetype(tmp, filetype):
    """Run the orchestrator for a single_print task whose attached file has
    the given filetype in the get_listing_files readback. Returns (report, etsy)."""
    design = _fake_image_path(tmp, "design.png")
    task = _make_done_task("Botanical Line Art Print", "single_print")
    orch = PipelineOrchestrator()
    pia, lga, pis, ms = _patch_common(tmp)
    etsy = FakeEtsyClientHappy()
    eis = MagicMock()

    async def _attach(listing_id, listing_image_paths, digital_file_path=None):
        return {
            "listing_id": listing_id,
            "uploaded_images": [{"path": p, "result": {"ok": True}} for p in listing_image_paths],
            "digital_upload": {"ok": True} if digital_file_path else None,
            "publish_result": {"published": True, "state": "active"},
        }

    async def _get_images(listing_id):
        return [{"listing_image_id": 1}]

    async def _get_files(listing_id):
        return [{"listing_file_id": 1, "filetype": filetype}]

    eis.return_value.attach_images_and_publish.side_effect = _attach
    eis.return_value.get_listing_images.side_effect = _get_images
    eis.return_value.get_listing_files.side_effect = _get_files

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms):
        report = orch.run_post_completion(task.id)
    return report, etsy


# ── [3] octet-stream file -> readback failure ───────────────────────────────
print("[3] file present but filetype octet-stream (won't display) -> listing deleted, blocked...")

with tempfile.TemporaryDirectory() as tmp:
    report3, etsy3 = _run_with_filetype(tmp, "application/octet-stream")

if (
    report3.get("blocked") is True
    and "unrecognised filetype" in (report3.get("blocked_reason") or "")
    and etsy3.deleted == etsy3.created
    and etsy3.created
):
    ok("[3] octet-stream-only file: listing deleted, task blocked (would have caught the real bug)")
else:
    fail("[3] octet-stream gate", f"report={report3}, created={etsy3.created}, deleted={etsy3.deleted}")


# ── [4] real MIME type file -> passes ───────────────────────────────────────
print("[4] file with real MIME type image/png -> passes the gate normally...")

with tempfile.TemporaryDirectory() as tmp:
    report4, etsy4 = _run_with_filetype(tmp, "image/png")

ap4 = report4["stages"].get("attach_publish", {})
if ap4.get("ok") is True and not report4.get("blocked") and not etsy4.deleted:
    ok("[4] image/png file passes the display gate, listing kept")
else:
    fail("[4] valid filetype passes", f"attach_publish={ap4}, blocked={report4.get('blocked')}, deleted={etsy4.deleted}")


# ── Summary ────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
