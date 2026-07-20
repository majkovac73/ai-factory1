"""
Step 100g test — digital single-image listing photos are built FROM the real
delivery design (mockups), not independent generations.

Context: live test of a real coloring_page task (e881c422) still BLOCKED at the
consistency gate — reason "marketing image 2: shows a different illustrated
dinosaur design". Root cause: hero/lifestyle were INDEPENDENT text-to-image
generations, so they depict a genuinely different illustration than the
delivered file; the consistency gate correctly rejects that and no prompt/remake
can fix it. Fix: for digital single-image formats, derive the listing photos
from the actual delivery asset via PIL mockups (the real design on clean
backgrounds), so every listing photo honestly depicts the delivered product and
the consistency gate passes truthfully.

Tests (PIL only — no image generation, no API):
  [1] _build_listing_mockups produces exactly 2 listing images (hero.png,
      lifestyle.png), each a valid 1024x1024 PNG.
  [2] The mockups actually CONTAIN the delivered design (centre pixels match the
      delivery colour; corners are the mockup background) — i.e. they depict the
      real product, so the consistency check will pass.
  [3] The mockups are registered in the catalog as 'listing' assets by the
      DeliveryMockup agent (not ProductImageAgent).
  [4] Robustness: if the delivery can't be opened, it returns [] and records the
      failure (the prepended delivery still stands as the listing photo).

Usage:
  python scripts/test_step100g_delivery_mockups.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test100g.db", delete=False)
_tmp.close()
os.environ.pop("DATABASE_PATH", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
# Deterministic scenes (no network): use the PIL studio/desk fallback background
# so the composite is reproducible in tests.
os.environ["MOCKUP_USE_GENERATED_SCENES"] = "false"

import logging
logging.basicConfig(level=logging.ERROR)

from dotenv import load_dotenv
load_dotenv()

from app.db.database import Base, engine
import app.models.task, app.models.log, app.models.image_asset
Base.metadata.create_all(bind=engine)

from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from PIL import Image as PILImage

from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.task_service import TaskService
from app.schemas.task import TaskCreate
from app.schemas.enums import TaskStatus
from app.services.content_quality_service import ContentQualityService

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 100g — delivery-derived listing mockups\n")

DELIVERY_COLOR = (200, 40, 40)  # a distinctive solid red delivery design


def _delivery_png(tmp, name="design.png", size=(1024, 1536)):
    # Non-square (2:3) to prove the mockup fits/centres any delivery aspect.
    p = Path(tmp) / name
    PILImage.new("RGB", size, DELIVERY_COLOR).save(p, format="PNG")
    return p


# ── [1] + [2] + [3] main path ────────────────────────────────────────────────
print("[1/2/3] _build_listing_mockups builds valid mockups that contain the real design...")

with tempfile.TemporaryDirectory() as tmp:
    delivery = _delivery_png(tmp)
    orch = PipelineOrchestrator()

    catalog_calls = []
    orig_register = orch.catalog.register
    def _rec_register(**kw):
        catalog_calls.append(kw)
        return orig_register(**kw)
    orch.catalog.register = _rec_register

    report = {"stages": {}}
    paths = orch._build_listing_mockups("task-100g", delivery, report)

    def _top_red_y(im, x, h):
        for y in range(0, h):
            px = im.getpixel((x, y))
            if px[0] > 150 and px[1] < 90 and px[2] < 90:
                return y
        return None

    # #8: mockups are now >=2000px, and the hero is LANDSCAPE (not 1:1). Checks
    # are RESOLUTION-RELATIVE (fractions of each image's own size) rather than
    # hardcoded 1024 coordinates.
    from config import settings as _settings
    names = sorted(Path(p).name for p in paths)
    dims_ok = True
    design_ok = bg_ok = no_wm_ok = angled_ok = True
    for p in paths:
        with PILImage.open(p) as im:
            im = im.convert("RGB")
            W, H = im.size
            is_hero = Path(p).name == "hero.png"
            # hero: landscape (LISTING_HERO_W x LISTING_HERO_H); others: square >=2000
            if is_hero:
                if (W, H) != (int(_settings.LISTING_HERO_W), int(_settings.LISTING_HERO_H)) or W <= H:
                    dims_ok = False
            else:
                if W != H or W < 2000:
                    dims_ok = False
            # [2] the composite CONTAINS the real design: the delivery red must
            #     be dominant in the central area where the print sits.
            red = total = 0
            for yy in range(int(H * 0.30), int(H * 0.70), max(1, H // 128)):
                for xx in range(int(W * 0.30), int(W * 0.70), max(1, W // 128)):
                    px = im.getpixel((xx, yy)); total += 1
                    if px[0] > 150 and px[1] < 90 and px[2] < 90:
                        red += 1
            if total == 0 or red < total * 0.25:
                design_ok = False
            # NO watermark: in a TIGHT central box (deep inside the solid design)
            # there should be ~no light pixels; a tiled watermark would blend many.
            light = ltot = 0
            for yy in range(int(H * 0.45), int(H * 0.55), max(1, H // 170)):
                for xx in range(int(W * 0.45), int(W * 0.55), max(1, W // 170)):
                    ltot += 1
                    px = im.getpixel((xx, yy))
                    if px[0] > 150 and px[1] > 110 and px[2] > 110:
                        light += 1
            if light > max(4, ltot * 0.05):
                no_wm_ok = False
            if im.getpixel((5, 5)) == DELIVERY_COLOR:
                bg_ok = False
            # [5b] ANGLED: the design's top edge is NOT horizontal (perspective /
            #      rotation), so a screenshot isn't a clean flat rectangle.
            xs = range(int(W * 0.34), int(W * 0.67), max(1, W // 40))
            tops = [t for t in (_top_red_y(im, x, H) for x in xs) if t is not None]
            if not tops or (max(tops) - min(tops)) < int(H * 0.01):
                angled_ok = False

    EXPECTED_MOCKUPS = {"hero.png", "lifestyle.png", "styled.png", "desk.png"}
    if len(paths) == 4 and set(names) == EXPECTED_MOCKUPS and dims_ok:
        ok("[1] four valid >=2000px listing mockups, landscape hero (A-8 + #8)")
    else:
        fail("[1] mockup files", f"paths={paths}, dims_ok={dims_ok}")

    if design_ok and bg_ok:
        ok("[2] mockups depict the real delivered design composited into a scene (design dominant centre, scene at edges)")
    else:
        fail("[2] mockup content", f"design_ok={design_ok}, bg_ok={bg_ok}")

    if no_wm_ok and angled_ok:
        ok("[5] mockups are NOT watermarked and the design is composited at an ANGLE (not a usable flat copy)")
    else:
        fail("[5] no-watermark + angled", f"no_wm_ok={no_wm_ok}, angled_ok={angled_ok}")

    # [3] registered as listing assets by DeliveryMockup, and stage reported
    listing_regs = [c for c in catalog_calls if c.get("variant") == "listing" and c.get("agent") == "DeliveryMockup"]
    stage = report["stages"].get("listing_images", {})
    if len(listing_regs) == 4 and stage.get("source") == "delivery_mockup" and stage.get("ok"):
        ok("[3] both mockups registered as 'listing' by DeliveryMockup; stage source=delivery_mockup")
    else:
        fail("[3] registration", f"listing_regs={len(listing_regs)}, stage={stage}")


# ── [4] robustness: unreadable delivery -> [] and recorded failure ───────────
print("[4] unreadable delivery -> returns [] and records failure (delivery still stands)...")

with tempfile.TemporaryDirectory() as tmp:
    bad = Path(tmp) / "notimage.png"
    bad.write_bytes(b"this is not an image")
    orch2 = PipelineOrchestrator()
    report2 = {"stages": {}}
    paths2 = orch2._build_listing_mockups("task-100g-bad", bad, report2)
    st2 = report2["stages"].get("listing_images", {})
    if paths2 == [] and st2.get("ok") is False:
        ok("[4] unreadable delivery handled gracefully (no crash, empty result, failure recorded)")
    else:
        fail("[4] robustness", f"paths={paths2}, stage={st2}")


# ── [6] SECURITY: the raw clean deliverable is NOT a public listing photo ─────
print("[6] the raw clean deliverable is NEVER uploaded as a public listing photo (only the angled scene mockups)...")

import app.models.task, app.models.log, app.models.image_asset, app.models.marketing_post  # noqa
from app.db.database import Base as _Base, engine as _engine
_Base.metadata.create_all(bind=_engine)


def _make_done_single_print(tmp):
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt="Botanical Line Art Print", type="single_print"))
    ts.update_status(t.id, TaskStatus.PLANNED.value)
    ts.update_status(t.id, TaskStatus.RUNNING.value)
    ts.update_status(t.id, TaskStatus.QA.value)
    ts.save_qa_result(t.id, output_data={"title": "Botanical Line Art Print",
                                         "description": "a botanical line art print", "keywords": ["art"], "sections": []},
                      error_message=None)
    ts.update_status(t.id, TaskStatus.DONE.value)
    return ts.get_task(t.id)


class _OkPOD:
    def __init__(self, design): self._d = design
    def build_product_record(self, task_id, product_name, visual_brief, product_type, **kwargs):
        return {"task_id": task_id, "design_path": str(self._d), "ready_for_pod": True}


class _CleanCQ:
    def __init__(self, *a, **k): pass
    def review_asset_file(self, *a, **k): return SimpleNamespace(passed=True, specific_issues=[])
    def review_asset_bytes(self, *a, **k): return SimpleNamespace(passed=True, specific_issues=[])
    def check_marketing_consistency(self, *a, **k): return SimpleNamespace(passed=True, specific_issues=[], mismatches=[])


class _FakeEtsy:
    def __init__(self): self._sent = {}
    async def create_draft_listing(self, listing): self._sent["L1"] = listing; return {"listing_id": "L1"}
    async def get_listing(self, lid): s = self._sent.get(lid, {}); return {"listing_id": lid, "taxonomy_id": s.get("taxonomy_id"), "when_made": s.get("when_made")}
    async def delete_listing(self, lid): return True


with tempfile.TemporaryDirectory() as tmp:
    design = _delivery_png(tmp, "design.png", size=(1024, 1024))  # real deliverable (square, as delivery validation requires)
    task = _make_done_single_print(tmp)

    captured = {}
    eis = MagicMock()
    async def _attach(listing_id, listing_image_paths, digital_file_path=None, **kwargs):
        captured["listing_photos"] = list(listing_image_paths)
        captured["digital_file"] = digital_file_path
        return {"listing_id": listing_id,
                "uploaded_images": [{"path": p, "result": {"ok": True}} for p in listing_image_paths],
                "digital_upload": {"ok": True} if digital_file_path else None,
                "publish_result": {"published": True, "state": "active"}}
    async def _gi(listing_id): return [{"listing_image_id": i} for i in range(len(captured.get("listing_photos", [])))]
    async def _gf(listing_id): return [{"listing_file_id": 1, "filetype": "image/png"}]
    eis.return_value.attach_images_and_publish.side_effect = _attach
    eis.return_value.get_listing_images.side_effect = _gi
    eis.return_value.get_listing_files.side_effect = _gf

    lga = MagicMock(); lga.return_value.generate_listing.return_value = {"title": "t"}
    pis = MagicMock(); pis.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(design)}
    ms = MagicMock(); ms.return_value.get_posts_for_task.return_value = []; ms.return_value.post_to_channel.return_value = {"success": True}

    orch = PipelineOrchestrator()
    with patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: _OkPOD(design)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=_FakeEtsy()), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", eis), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms), \
         patch("app.services.content_quality_service.ContentQualityService", _CleanCQ):
        report6 = orch.run_post_completion(task.id)

    photos = [Path(p).name for p in captured.get("listing_photos", [])]
    design_str = str(design)
    raw_not_public = design_str not in [str(p) for p in captured.get("listing_photos", [])]
    only_mockups = set(photos) == {"hero.png", "lifestyle.png", "styled.png", "desk.png"}
    clean_is_digital_file = str(captured.get("digital_file")) == design_str

    if raw_not_public and only_mockups and clean_is_digital_file:
        ok("[6] listing photos are ONLY the angled scene mockups; the raw clean file is the buyer-gated digital file, never a public photo")
    else:
        fail("[6] product exposure", f"photos={photos}, raw_not_public={raw_not_public}, "
                                     f"clean_is_digital_file={clean_is_digital_file}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

try:
    os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
