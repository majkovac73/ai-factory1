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

import logging
logging.basicConfig(level=logging.ERROR)

from dotenv import load_dotenv
load_dotenv()

from app.db.database import Base, engine
import app.models.task, app.models.log, app.models.image_asset
Base.metadata.create_all(bind=engine)

from PIL import Image as PILImage

from app.services.pipeline_orchestrator import PipelineOrchestrator

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

    # [1] two valid 1024x1024 PNG listing images named hero/lifestyle
    names = sorted(Path(p).name for p in paths)
    dims_ok = True
    center_ok = corner_ok = True
    for p in paths:
        with PILImage.open(p) as im:
            im = im.convert("RGB")
            if im.size != (1024, 1024):
                dims_ok = False
            # [2] centre pixel = the delivery colour; a corner = the mockup bg
            cx = im.getpixel((512, 512))
            corner = im.getpixel((5, 5))
            if not (abs(cx[0] - DELIVERY_COLOR[0]) < 25 and abs(cx[1] - DELIVERY_COLOR[1]) < 25 and abs(cx[2] - DELIVERY_COLOR[2]) < 25):
                center_ok = False
            if corner == DELIVERY_COLOR:   # corner must be background, not the design
                corner_ok = False

    if len(paths) == 2 and names == ["hero.png", "lifestyle.png"] and dims_ok:
        ok("[1] two valid 1024x1024 listing mockups (hero.png, lifestyle.png)")
    else:
        fail("[1] mockup files", f"paths={paths}, dims_ok={dims_ok}")

    if center_ok and corner_ok:
        ok("[2] mockups contain the real delivered design (centre matches delivery, corners are background)")
    else:
        fail("[2] mockup content", f"center_ok={center_ok}, corner_ok={corner_ok}")

    # [3] registered as listing assets by DeliveryMockup, and stage reported
    listing_regs = [c for c in catalog_calls if c.get("variant") == "listing" and c.get("agent") == "DeliveryMockup"]
    stage = report["stages"].get("listing_images", {})
    if len(listing_regs) == 2 and stage.get("source") == "delivery_mockup" and stage.get("ok"):
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


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

try:
    os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
