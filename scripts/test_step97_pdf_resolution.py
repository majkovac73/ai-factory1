"""
Step 97 test — PDF page generation must request a Seedream-valid image size.

Root cause of the production failure (task 127d5130, "Mindfulness Daily
Planner"): PDFGenerationService requested every page at aspect_ratio="2:3",
resolution="2K". Seedream 4.5 rejects any request below 3,686,400 pixels with

    OpenRouter Image API error 400: "The parameter `size` specified in the
    request is not valid: image size must be at least 3686400 pixels."

A 2:3 page at "2K" is only ~2.8M pixels — below the floor — so page 1/6 failed
immediately and the whole task was blocked. The fix moves PDF pages to "4K"
(2:3 @ 4K = 2732×4096 = ~11.2M pixels, well above the floor), matching the
identical 2K→4K fix already applied to the Pinterest pin.

This never surfaced in the step-91 suite because its FakeImageProvider ignores
resolution/aspect_ratio and always returns a valid image. This suite uses a
*resolution-aware* fake provider that reproduces the exact Seedream 400 for an
under-sized request, so it would have caught the bug — and confirms the fix's
chosen size (4K) does not trigger it.

Covers:
  [1] The resolution-aware fake provider genuinely rejects the OLD setting
      (2:3 @ 2K) with the exact Seedream 400 — proves the simulation is real,
      not a no-op that would pass regardless.
  [2] With the actual (fixed) PDFGenerationService code, all pages generate
      against that same strict provider, and a real multi-page PDF is
      assembled and readback-verified. No 400 is raised.
  [3] Safety net preserved: a genuine image-generation failure (provider
      raises for a non-size reason) still fails the whole PDF — no partial
      PDF is ever produced.

Usage:
  python scripts/test_step97_pdf_resolution.py
"""
import base64
import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))

import logging
logging.basicConfig(level=logging.WARNING)

from PIL import Image as PILImage

from app.services.pdf_generation_service import (
    PDFGenerationService,
    PDFGenerationError,
    PDF_PAGE_ASPECT_RATIO,
    PDF_PAGE_RESOLUTION,
)

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 97 — PDF page image size must satisfy Seedream's pixel floor\n")

SEEDREAM_MIN_PIXELS = 3_686_400
# Seedream sets the LONGER edge from the resolution tier, then scales the
# shorter edge by the aspect ratio (confirmed from real API calls recorded in
# CHANGELOG_AUTOMATED.md: 1:1@2K=2048×2048, 2:3@4K=2732×4096).
_TIER_LONG_EDGE = {"512": 512, "1K": 1024, "2K": 2048, "4K": 4096}


def _requested_pixels(aspect_ratio: str, resolution: str) -> int:
    """Reproduce the width×height Seedream would allocate for a request."""
    w_ratio, h_ratio = (int(x) for x in aspect_ratio.split(":"))
    long_edge = _TIER_LONG_EDGE[resolution]
    if w_ratio >= h_ratio:
        width = long_edge
        height = round(long_edge * h_ratio / w_ratio)
    else:
        height = long_edge
        width = round(long_edge * w_ratio / h_ratio)
    return width * height


class _FakeImageResult:
    def __init__(self, b64):
        self.b64_data = b64
        self.url = None


class ResolutionAwareFakeProvider:
    """Fake image provider that enforces Seedream's real 3,686,400-pixel floor.

    For an under-sized request it raises the *exact* HTTP 400 the real provider
    raised in production. Otherwise it returns a small but valid PNG (the 400 is
    about the requested size, not the returned bytes, so a downscaled stand-in
    keeps the test fast without weakening it)."""

    def __init__(self):
        self.calls = 0
        self.seen = []

    async def generate_image(self, prompt, aspect_ratio="1:1", resolution="1K", **kw):
        self.calls += 1
        self.seen.append((aspect_ratio, resolution))
        pixels = _requested_pixels(aspect_ratio, resolution)
        if pixels < SEEDREAM_MIN_PIXELS:
            raise RuntimeError(
                f"OpenRouter Image API error 400: {{\"error\": {{\"message\": "
                f"\"The parameter `size` specified in the request is not valid: "
                f"image size must be at least {SEEDREAM_MIN_PIXELS} pixels.\"}}}}"
            )
        img = PILImage.new("RGB", (400, 600), color=(40 + self.calls * 15 % 200, 90, 160))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return _FakeImageResult(base64.b64encode(buf.getvalue()).decode())


class AlwaysFailProvider:
    """Genuine generation failure unrelated to image size (the safety net)."""

    def __init__(self):
        self.calls = 0

    async def generate_image(self, prompt, **kw):
        self.calls += 1
        raise RuntimeError("simulated upstream generation outage (not a size error)")


class _PassCQ:
    """Content-QA double: always passes, never makes a real vision call."""
    def review_asset_bytes(self, *a, **k):
        class _R:
            passed = True
            specific_issues = []
        return _R()


PAGE_BRIEFS = ["Cover page with title", "Weekly mindfulness grid", "Daily reflection prompts"]


# ── [1] the fake provider really rejects the OLD (buggy) setting ─────────────
print("[1] resolution-aware provider rejects the OLD setting (2:3 @ 2K) with Seedream's 400...")

_probe = ResolutionAwareFakeProvider()
import asyncio
try:
    asyncio.run(_probe.generate_image("x", aspect_ratio="2:3", resolution="2K"))
    fail("[1] simulation is real", "provider did NOT reject 2:3 @ 2K — simulation is a no-op")
except RuntimeError as e:
    if "at least 3686400 pixels" in str(e) and _requested_pixels("2:3", "2K") < SEEDREAM_MIN_PIXELS:
        ok(f"[1] 2:3 @ 2K = {_requested_pixels('2:3', '2K'):,} px correctly rejected with the exact 400")
    else:
        fail("[1] simulation is real", f"unexpected error: {e}")


# ── [2] the fixed service generates all pages against that same strict provider ──
print("[2] fixed PDFGenerationService: all pages clear the floor, real PDF assembled + readback-verified...")

# Guard: the fix must actually request a size at/above the floor.
if _requested_pixels(PDF_PAGE_ASPECT_RATIO, PDF_PAGE_RESOLUTION) >= SEEDREAM_MIN_PIXELS:
    ok(
        f"[2a] PDF pages request {PDF_PAGE_ASPECT_RATIO} @ {PDF_PAGE_RESOLUTION} = "
        f"{_requested_pixels(PDF_PAGE_ASPECT_RATIO, PDF_PAGE_RESOLUTION):,} px (>= {SEEDREAM_MIN_PIXELS:,} floor)"
    )
else:
    fail("[2a] PDF page request size", f"{PDF_PAGE_ASPECT_RATIO} @ {PDF_PAGE_RESOLUTION} is below the floor")

with tempfile.TemporaryDirectory() as tmp:
    os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(tmp, "images")
    provider = ResolutionAwareFakeProvider()
    svc = PDFGenerationService(image_provider=provider, content_quality_service=_PassCQ())
    try:
        pdf_path = svc.generate_pdf(
            task_id="step97-ok",
            product_name="Mindfulness Daily Planner",
            visual_brief="calm, minimalist, soft pastel palette",
            page_briefs=PAGE_BRIEFS,
        )
        from pypdf import PdfReader
        n = len(PdfReader(str(pdf_path)).pages)
        all_4k = all(r == PDF_PAGE_RESOLUTION for _, r in provider.seen)
        if pdf_path.exists() and n == len(PAGE_BRIEFS) and all_4k:
            ok(f"[2b] {n}-page PDF generated with no 400; every page requested at {PDF_PAGE_RESOLUTION}")
        else:
            fail("[2b] fixed PDF generation", f"pages={n}, exists={pdf_path.exists()}, seen={provider.seen}")
    except PDFGenerationError as e:
        fail("[2b] fixed PDF generation", f"unexpected PDFGenerationError: {e}")


# ── [3] safety net: a genuine generation failure still blocks the whole PDF ──
print("[3] safety net: a genuine (non-size) generation failure still fails the whole PDF...")

with tempfile.TemporaryDirectory() as tmp:
    os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(tmp, "images")
    failer = AlwaysFailProvider()
    svc = PDFGenerationService(image_provider=failer, content_quality_service=_PassCQ())
    try:
        svc.generate_pdf(
            task_id="step97-fail",
            product_name="Mindfulness Daily Planner",
            visual_brief="calm, minimalist",
            page_briefs=PAGE_BRIEFS,
        )
        fail("[3] safety net", "generate_pdf did NOT raise on a genuine generation failure")
    except PDFGenerationError as e:
        if failer.calls == 1 and "generation failed" in str(e):
            ok("[3] genuine generation failure on page 1 blocks the whole PDF (no partial output)")
        else:
            fail("[3] safety net", f"calls={failer.calls}, err={e}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")
sys.exit(0 if _failed == 0 else 1)
