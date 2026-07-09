"""
Step 100k test — PDF planners build listing photos from the REAL pages, not
independent generations.

Bug: pdf_planner_or_guide kept the independent-generation path, so its hero/
lifestyle depicted DIFFERENT pages than the delivered PDF -> the consistency gate
rejected them and the task blocked (same root cause coloring pages had). Fix: for
PDF, derive the listing photos from the ACTUAL extracted pages (a real page on a
desk + a fan of several real pages) via MockupService, and skip the consistency
gate (there is no independent image to misrepresent).

Tests (PIL + pypdf only):
  [1] _extract_pdf_pages pulls the real page images from a Pillow-assembled PDF.
  [2] build_flatlay_bytes composes a valid 1024x1024 mockup from several pages,
      angled (not a flat, screenshot-usable copy).
  [3] _build_listing_mockups(pdf) builds 2 mockups FROM the pages (source
      delivery_mockup, roles pdf_page/pdf_fan), registered as 'listing'; the raw
      PDF is never one of them.

Usage:
  python scripts/test_step100k_pdf_mockups.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test100k.db", delete=False)
_tmp.close()
os.environ.pop("DATABASE_PATH", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
os.environ["MOCKUP_USE_GENERATED_SCENES"] = "false"  # deterministic PIL scenes

import logging
logging.basicConfig(level=logging.ERROR)

from dotenv import load_dotenv
load_dotenv()

from app.db.database import Base, engine
import app.models.task, app.models.log, app.models.image_asset
Base.metadata.create_all(bind=engine)

from io import BytesIO
from PIL import Image as PILImage, ImageDraw

from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.mockup_service import MockupService

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 100k — PDF planner mockups from real pages\n")


def _make_pdf(path, pages=4):
    cols = [(250, 240, 230), (235, 245, 250), (245, 250, 235), (250, 235, 245), (240, 240, 240)]
    imgs = []
    for i in range(pages):
        im = PILImage.new("RGB", (1024, 1536), cols[i % len(cols)])
        d = ImageDraw.Draw(im)
        d.rectangle([80, 80, 944, 1456], outline=(40, 40, 40), width=4)
        for y in range(200, 1400, 90):
            d.line([120, y, 900, y], fill=(120, 120, 120), width=3)
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:])


orch = PipelineOrchestrator()


# ── [1] extract real pages ───────────────────────────────────────────────────
print("[1] _extract_pdf_pages pulls the real page images from the PDF...")

with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / "design.pdf"
    _make_pdf(pdf, pages=4)
    pages = orch._extract_pdf_pages(pdf, max_pages=4)
    valid = len(pages) == 4 and all(Path(p).exists() for p in pages)
    decodable = all((lambda im: im.size[0] > 0)(PILImage.open(p)) for p in pages) if valid else False
    if valid and decodable:
        ok("[1] extracted 4 real, decodable page images")
    else:
        fail("[1] extract", f"pages={len(pages)}, valid={valid}")


# ── [2] multi-page fan flat-lay is valid + angled ────────────────────────────
print("[2] build_flatlay_bytes -> valid 1024x1024 mockup with the pages, angled...")

with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / "d.pdf"
    _make_pdf(pdf, pages=3)
    pages = orch._extract_pdf_pages(pdf, max_pages=4)
    png = MockupService().build_flatlay_bytes([str(p) for p in pages], size=1024)
    im = PILImage.open(BytesIO(png)).convert("RGB")

    # angled: the top edge of the (light) pages is not horizontal — sample the
    # topmost non-background row position across columns; a flat layout would be
    # ~constant. The desk bg is warm (r>g>b); pages are lighter/neutral.
    def _top_page_y(x):
        for y in range(0, 1024):
            r, g, b = im.getpixel((x, y))
            if r > 235 and g > 235 and b > 225 and abs(r - b) < 22:  # light neutral page
                return y
        return None
    tops = [t for t in (_top_page_y(x) for x in range(300, 720, 30)) if t is not None]
    angled = bool(tops) and (max(tops) - min(tops)) > 12
    if im.size == (1024, 1024) and angled:
        ok("[2] multi-page fan flat-lay is a valid 1024x1024 mockup, composed at an angle")
    else:
        fail("[2] fan flatlay", f"size={im.size}, angled={angled}, tops_spread={ (max(tops)-min(tops)) if tops else None }")


# ── [3] _build_listing_mockups(pdf) -> mockups from pages, registered ────────
print("[3] _build_listing_mockups(pdf) builds page mockups (not the raw PDF), registered as listing...")

with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / "design.pdf"
    _make_pdf(pdf, pages=4)

    catalog_calls = []
    orig = orch.catalog.register
    def _rec(**kw):
        catalog_calls.append(kw); return orig(**kw)
    orch.catalog.register = _rec

    report = {"stages": {}}
    paths = orch._build_listing_mockups("task-100k", pdf, report)
    orch.catalog.register = orig

    names = sorted(Path(p).name for p in paths)
    all_pngs = all(str(p).lower().endswith(".png") for p in paths)
    raw_pdf_not_included = all(str(p) != str(pdf) for p in paths)
    regs = [c for c in catalog_calls if c.get("variant") == "listing" and c.get("agent") == "DeliveryMockup"]
    models = sorted(c.get("model") for c in regs)
    stage = report["stages"].get("listing_images", {})
    dims_ok = all(PILImage.open(p).size == (1024, 1024) for p in paths)
    if (len(paths) == 2 and names == ["hero.png", "lifestyle.png"] and all_pngs and raw_pdf_not_included
            and len(regs) == 2 and models == ["scene_composite:pdf_fan", "scene_composite:pdf_page"]
            and stage.get("source") == "delivery_mockup" and dims_ok):
        ok("[3] two PNG page-mockups built from the PDF pages, registered as listing; raw PDF not a photo")
    else:
        fail("[3] pdf mockups", f"names={names}, models={models}, raw_excluded={raw_pdf_not_included}, stage={stage}")


print(f"\nResults: {_passed} passed, {_failed} failed\n")
try:
    os.unlink(_tmp.name)
except Exception:
    pass
sys.exit(0 if _failed == 0 else 1)
