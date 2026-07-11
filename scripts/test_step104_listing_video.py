"""
Step 104 test — 3-4 ken-burns listing video render + Etsy upload wiring.

Usage: python scripts/test_step104_listing_video.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── render a real MP4 from a source image ──
from app.services.listing_video_service import ListingVideoService

tmp = tempfile.mkdtemp()
src = os.path.join(tmp, "design.png")
# a non-trivial gradient so frames actually differ
img = Image.new("RGB", (1024, 1024))
px = img.load()
for y in range(1024):
    for x in range(0, 1024, 8):
        c = ((x + y) % 255, (x * 2) % 255, (y * 2) % 255)
        for dx in range(8):
            px[x + dx, y] = c
img.save(src)

out = os.path.join(tmp, "out.mp4")
# short clip to keep the test fast
svc = ListingVideoService(fps=12, seconds=1.5, zoom=0.15)
res = svc.render(src, out, out_size=(480, 480))

check("3-4 render returns the output path", res == out)
check("3-4 MP4 file exists", os.path.exists(out))
size = os.path.getsize(out) if os.path.exists(out) else 0
check(f"3-4 MP4 is non-trivial (>2KB, got {size})", size > 2048)
# MP4 signature: bytes 4-8 are 'ftyp'
with open(out, "rb") as f:
    head = f.read(12)
check("3-4 file has an MP4/ftyp container signature", b"ftyp" in head)

# ── a single frame is deterministic & correctly sized ──
base = Image.open(src).convert("RGB")
f0 = svc._frame(base, 0.0, 480, 480)
f1 = svc._frame(base, 1.0, 480, 480)
check("3-4 frame is the requested output size", f0.size == (480, 480))
check("3-4 zoom actually changes the frame across time", list(f0.getdata()) != list(f1.getdata()))

# ── Etsy client exposes upload_listing_video ──
from app.services.etsy_image_service import EtsyImageService
check("3-4 EtsyImageService.upload_listing_video exists",
      callable(getattr(EtsyImageService, "upload_listing_video", None)))

# ── pipeline stage is gated off by default (no publish behavior change) ──
from unittest.mock import MagicMock
from app.services.pipeline_orchestrator import PipelineOrchestrator
orch = PipelineOrchestrator()
rep = {"stages": {}}
orch._stage_listing_video("task-x", "listing-1", rep)
check("3-4 stage skips when LISTING_VIDEO_ENABLED is off (default)",
      rep["stages"].get("listing_video", {}).get("skipped") == "LISTING_VIDEO_ENABLED off")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104 (3-4) tests passed.")
