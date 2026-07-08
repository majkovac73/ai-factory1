"""
Step 95 test — digital listings get a non-made_to_order when_made + readback

Context: listing 4534427807's digital file was attached AND correctly typed
(image/png, step 94) yet still did not appear in Etsy's listing editor. Root
cause, confirmed by a live single-variable experiment (changing only
when_made and Maj re-checking the editor): the listing had
when_made="made_to_order", which Etsy treats as a personalized/custom
digital item delivered manually after purchase — so the editor hides the
instant-download file slot even though a file is attached. Our
create_draft_listing() hardcoded when_made="made_to_order" for every
listing. That value IS correct for POD physical goods (printed after
purchase) but wrong for instant digital downloads.

Tests:
  [1] create_draft_listing() sends the caller's when_made (not a hardcoded
      made_to_order), defaulting to POD_WHEN_MADE only when unspecified.
  [2] Orchestrator sends a non-made_to_order when_made for a DIGITAL
      product (single_print) and made_to_order for a POD product
      (pod_apparel_design).
  [3] Orchestrator create-time readback: the real listing's when_made is
      made_to_order for a digital product (Etsy silently kept/overrode it)
      -> listing deleted, task blocked (would have caught the real bug).

Uses test doubles throughout — no real Etsy API calls.

Usage:
  python scripts/test_step95_when_made.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test95.db", delete=False)
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
from app.services.etsy_client import DIGITAL_WHEN_MADE, POD_WHEN_MADE

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 95 — digital when_made (not made_to_order) + readback tests\n")

import asyncio


# ── [1] create_draft_listing sends the caller's when_made ────────────────────
print("[1] create_draft_listing() uses listing['when_made'], not a hardcoded made_to_order...")


async def _run_test_1():
    from app.services.etsy_client import EtsyClient
    captured = {}

    class _Resp:
        status_code = 201
        def json(self): return {"listing_id": "L1"}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured.update(json)
            return _Resp()

    with patch("app.services.etsy_client.get_valid_access_token", return_value="tok"), \
         patch("app.services.etsy_client.httpx.AsyncClient", _Client):
        # explicit when_made honoured
        await EtsyClient().create_draft_listing({"title": "x", "when_made": DIGITAL_WHEN_MADE, "type": "download"})
        explicit = captured.get("when_made")
        captured.clear()
        # default when unspecified
        await EtsyClient().create_draft_listing({"title": "x", "type": "download"})
        default = captured.get("when_made")
    return explicit, default


explicit1, default1 = asyncio.run(_run_test_1())
if explicit1 == DIGITAL_WHEN_MADE and explicit1 != "made_to_order" and default1 == POD_WHEN_MADE:
    ok(f"[1] sends caller's when_made ({explicit1}); defaults to {default1} when unspecified")
else:
    fail("[1] create_draft_listing when_made", f"explicit={explicit1}, default={default1}")


# ── Helpers ─────────────────────────────────────────────────────────────────


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


class OkPODFulfillment:
    def create_product_for_task(self, task_id, etsy_listing_id=None):
        class _P: id = "pod-1"
        return _P()
    def set_etsy_listing_id(self, pod_product_id, etsy_listing_id):
        return True


def _patch_common(tmp):
    hero = _fake_image_path(tmp, "hero.png")
    pia = MagicMock(); pia.return_value.generate_listing_images.return_value = {"hero": hero, "lifestyle": None}
    lga = MagicMock(); lga.return_value.generate_listing.return_value = {"title": "t"}
    pis = MagicMock(); pis.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(hero)}
    ms = MagicMock(); ms.return_value.get_posts_for_task.return_value = []; ms.return_value.post_to_channel.return_value = {"success": True}
    return pia, lga, pis, ms


def _fake_eis():
    m = MagicMock()
    async def _attach(listing_id, listing_image_paths, digital_file_path=None):
        return {"listing_id": listing_id,
                "uploaded_images": [{"path": p, "result": {"ok": True}} for p in listing_image_paths],
                "digital_upload": {"ok": True} if digital_file_path else None,
                "publish_result": {"published": True, "state": "active"}}
    async def _gi(listing_id): return [{"listing_image_id": 1}]
    async def _gf(listing_id): return [{"listing_file_id": 1, "filetype": "image/png"}]
    m.return_value.attach_images_and_publish.side_effect = _attach
    m.return_value.get_listing_images.side_effect = _gi
    m.return_value.get_listing_files.side_effect = _gf
    return m


class _EchoEtsy:
    """Echoes back what create sent (happy path)."""
    def __init__(self):
        self.deleted = []
        self._sent = {}
    async def create_draft_listing(self, listing):
        self._sent = listing
        return {"listing_id": "L-echo"}
    async def get_listing(self, listing_id):
        return {"listing_id": listing_id, "taxonomy_id": self._sent.get("taxonomy_id"), "when_made": self._sent.get("when_made")}
    async def delete_listing(self, listing_id):
        self.deleted.append(listing_id); return True


# ── [2] digital -> non-made_to_order; pod -> made_to_order ───────────────────
print("[2] orchestrator sends non-made_to_order for digital, made_to_order for POD...")

results2 = {}
for task_type, is_pod in [("single_print", False), ("pod_apparel_design", True)]:
    with tempfile.TemporaryDirectory() as tmp:
        design = _fake_image_path(tmp, "d.png")
        task = _make_done_task(f"Test {task_type}", task_type)
        orch = PipelineOrchestrator()
        pia, lga, pis, ms = _patch_common(tmp)
        etsy = _EchoEtsy()
        m_ship = MagicMock()
        async def _get_or_create():
            return None
        m_ship.return_value.get_or_create.side_effect = _get_or_create
        with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia), \
             patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design)), \
             patch("app.services.pipeline_orchestrator.PODFulfillmentService", lambda *a, **kw: OkPODFulfillment()), \
             patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga), \
             patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy), \
             patch("app.services.pipeline_orchestrator.EtsyImageService", _fake_eis()), \
             patch("app.services.etsy_shipping_service.EtsyShippingService", m_ship), \
             patch("app.services.pipeline_orchestrator.PinterestImageService", pis), \
             patch("app.services.pipeline_orchestrator.MarketingService", ms):
            orch.run_post_completion(task.id)
        results2[task_type] = etsy._sent.get("when_made")

if (
    results2.get("single_print") == DIGITAL_WHEN_MADE
    and results2.get("single_print") != "made_to_order"
    and results2.get("pod_apparel_design") == POD_WHEN_MADE
):
    ok(f"[2] digital when_made={results2['single_print']}, POD when_made={results2['pod_apparel_design']}")
else:
    fail("[2] when_made per type", f"results={results2}")


# ── [3] readback catches made_to_order on a digital listing ─────────────────
print("[3] digital listing readback shows made_to_order -> listing deleted, blocked...")

with tempfile.TemporaryDirectory() as tmp:
    design3 = _fake_image_path(tmp, "d3.png")
    task3 = _make_done_task("Botanical Print", "single_print")
    orch3 = PipelineOrchestrator()
    pia3, lga3, pis3, ms3 = _patch_common(tmp)
    delete_calls3 = []

    from app.core.product_formats import PRODUCT_FORMATS
    single_print_tax = PRODUCT_FORMATS["single_print"]["taxonomy_id"]

    class _StuckMadeToOrderEtsy:
        # Orchestrator instantiates EtsyClient() freshly per call, so this
        # holds no instance state: taxonomy readback passes (correct leaf),
        # then when_made readback catches the stuck made_to_order.
        async def create_draft_listing(self, listing):
            return {"listing_id": "L-stuck"}
        async def get_listing(self, listing_id):
            return {"listing_id": listing_id, "taxonomy_id": single_print_tax, "when_made": "made_to_order"}
        async def delete_listing(self, listing_id):
            delete_calls3.append(listing_id); return True

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia3), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design3)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga3), \
         patch("app.services.pipeline_orchestrator.EtsyClient", _StuckMadeToOrderEtsy), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis3), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms3):
        report3 = orch3.run_post_completion(task3.id)

    if (
        report3.get("blocked") is True
        and "when_made mismatch" in (report3.get("blocked_reason") or "")
        and delete_calls3 == ["L-stuck"]
    ):
        ok("[3] made_to_order on a digital listing: listing deleted, task blocked")
    else:
        fail("[3] when_made readback", f"report={report3}, deletes={delete_calls3}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
