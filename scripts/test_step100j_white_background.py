"""
Step 100j test — coloring-page delivery files get a PURE WHITE background.

Bug (Maj, live): a coloring_page delivery asset had a faint grey "transparency"
CHECKERBOARD baked into the RGB background (a Seedream artifact), so the buyer's
actual downloaded file wasn't clean white. `_flatten_white_background` whitens the
near-white/checkerboard background to pure white in the delivered file while
preserving the black line art exactly, and `_stage_pod_design` applies it for
coloring pages (only).

Tests (PIL only):
  [1] The near-white/checkerboard background is flattened to pure white, and the
      black line art is preserved exactly (same dark-pixel count).
  [2] Real content is NOT touched: a pixel with any dark/coloured channel
      (min < 234) is preserved — only near-white-in-all-channels pixels whiten.
  [3] The pipeline applies it for coloring_page and NOT for other formats.

Usage:
  python scripts/test_step100j_white_background.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))

import logging
logging.basicConfig(level=logging.ERROR)

from PIL import Image, ImageChops

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


print("\nStep 100j — coloring-page white background\n")


def _min_channel(im):
    r, g, b = im.split()
    return ImageChops.darker(ImageChops.darker(r, g), b)


def _dark_count(im, thr=120):
    return sum(1 for v in _min_channel(im).getdata() if v < thr)


orch = PipelineOrchestrator()


# ── [1] checkerboard bg -> pure white; line art preserved ────────────────────
print("[1] faint checkerboard background flattened to pure white; line art preserved...")

with tempfile.TemporaryDirectory() as tmp:
    im = Image.new("RGB", (200, 200), (255, 255, 255))
    px = im.load()
    for y in range(200):
        for x in range(200):
            if (x // 12 + y // 12) % 2 == 0:
                px[x, y] = (245, 245, 245)  # faint grey checkerboard square
    for x in range(20, 180):               # a black line-art stroke
        for dy in range(-2, 3):
            px[x, 100 + dy] = (0, 0, 0)
    p = Path(tmp) / "design.png"
    im.save(p)

    dark_before = _dark_count(Image.open(p).convert("RGB"))
    orch._flatten_white_background(p)
    out = Image.open(p).convert("RGB")

    bg_white = all(out.getpixel(pt) == (255, 255, 255) for pt in [(5, 5), (30, 5), (5, 30), (199 - 5, 5), (100, 5)])
    line_kept = out.getpixel((100, 100)) == (0, 0, 0)
    dark_after = _dark_count(out)
    if bg_white and line_kept and dark_after == dark_before:
        ok("[1] background is pure white; black line art preserved exactly (dark pixels unchanged)")
    else:
        fail("[1] flatten", f"bg_white={bg_white}, line_kept={line_kept}, dark {dark_before}->{dark_after}")


# ── [2] coloured/content pixels are NOT whitened ─────────────────────────────
print("[2] real content (a channel < 234) is preserved; only near-white whitens...")

with tempfile.TemporaryDirectory() as tmp:
    im = Image.new("RGB", (60, 60), (245, 245, 245))  # near-white bg
    im.putpixel((10, 10), (200, 220, 255))   # light blue content (min 200) -> keep
    im.putpixel((20, 20), (240, 240, 240))   # near-white (min 240) -> whiten
    im.putpixel((30, 30), (10, 10, 10))      # black line -> keep
    p = Path(tmp) / "d.png"
    im.save(p)
    orch._flatten_white_background(p)
    out = Image.open(p).convert("RGB")
    if (out.getpixel((10, 10)) == (200, 220, 255)
            and out.getpixel((20, 20)) == (255, 255, 255)
            and out.getpixel((30, 30)) == (10, 10, 10)
            and out.getpixel((0, 0)) == (255, 255, 255)):
        ok("[2] coloured/dark content preserved; near-white bg whitened")
    else:
        fail("[2] content-safe", f"blue={out.getpixel((10,10))}, nearwhite={out.getpixel((20,20))}, black={out.getpixel((30,30))}")


# ── [3] pipeline applies it for coloring_page only ───────────────────────────
print("[3] _stage_pod_design flattens the background for coloring_page, not other formats...")


class _FakePOD:
    def __init__(self, design): self._d = design
    def build_product_record(self, task_id, product_name, visual_brief, product_type):
        return {"task_id": task_id, "design_path": str(self._d), "ready_for_pod": True}


def _run_stage(task_type):
    tmp = tempfile.mkdtemp()
    design = Path(tmp) / "design.png"
    Image.new("RGB", (1024, 1024), (245, 245, 245)).save(design)  # square, near-white
    calls = []
    orch2 = PipelineOrchestrator()
    real_flatten = orch2._flatten_white_background
    def _spy(path):
        calls.append(str(path))
        return real_flatten(path)
    orch2._flatten_white_background = _spy
    report = {"stages": {}}
    with patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: _FakePOD(design)), \
         patch.object(orch2, "catalog"):
        orch2._stage_pod_design("t", "P", "brief", task_type, report)
    return calls


cp_calls = _run_stage("coloring_page")
sp_calls = _run_stage("single_print")
if len(cp_calls) == 1 and len(sp_calls) == 0:
    ok("[3] coloring_page -> background flattened; single_print -> not flattened")
else:
    fail("[3] format gating", f"coloring_page calls={len(cp_calls)}, single_print calls={len(sp_calls)}")


print(f"\nResults: {_passed} passed, {_failed} failed\n")
sys.exit(0 if _failed == 0 else 1)
