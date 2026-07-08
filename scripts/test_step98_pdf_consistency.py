"""
Step 98 test — marketing/deliverable consistency gate must accept PDF deliverables.

Follow-up to step 97. Once the Seedream size fix let PDF products actually
generate, a real prod run (task 3c5180db) got further and then blocked at
BLOCKED_NO_PRODUCT in the marketing/deliverable consistency gate:

    ContentQualityService: consistency vision call failed: Error code: 400
    "You uploaded an unsupported image. Please make sure your image has one of
     the following formats: ['jpeg', 'webp', 'gif', 'png']" (invalid_image_format)

Root cause: check_marketing_consistency() sent the delivery file's raw bytes
to the vision model as image/png. For a PDF deliverable those are PDF bytes —
Pillow can't decode them, so _downscale_for_review fell back to the raw PDF
bytes, which the vision provider rejects. This blocked EVERY
pdf_planner_or_guide product before listing creation.

Fix: _delivery_image_bytes() extracts the first page's embedded image via
pypdf (our PDFs are Pillow-assembled, one full-page image per page) and
re-encodes it as PNG — no PDF rasterizer / system dependency.

Covers:
  [1] Necessity: raw PDF bytes are NOT a decodable image (reproduces exactly
      what the provider rejected), while _delivery_image_bytes(pdf) returns
      decodable PNG bytes.
  [2] check_marketing_consistency on a PDF delivery sends the vision model
      only decodable images (the PDF converted to PNG + the marketing PNGs),
      never raw PDF bytes — and the gate passes.
  [3] Regression: a single-image (PNG) delivery is still handled unchanged.

Usage:
  python scripts/test_step98_pdf_consistency.py
"""
import base64
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import logging
logging.basicConfig(level=logging.ERROR)

from PIL import Image as PILImage

from app.services.content_quality_service import ContentQualityService, _delivery_image_bytes

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 98 — marketing/deliverable consistency gate accepts PDF deliverables\n")


def _is_decodable_image(raw: bytes) -> bool:
    try:
        PILImage.open(BytesIO(raw)).convert("RGB")
        return True
    except Exception:
        return False


def _make_pdf(path: Path, pages=6):
    """Build an image-only PDF exactly like PDFGenerationService does."""
    imgs = [PILImage.new("RGB", (800, 1200), color=(40 + i * 30, 90, 160)) for i in range(pages)]
    imgs[0].save(path, format="PDF", save_all=True, append_images=imgs[1:])


def _make_png(path: Path):
    PILImage.new("RGB", (1024, 1024), color=(120, 60, 180)).save(path, format="PNG")


class FakeVisionProvider:
    """Stands in for the real vision provider. It mirrors the real provider's
    hard requirement: every image it receives MUST be a decodable image — if
    any data URL carries non-image bytes (e.g. raw PDF), it raises the same
    kind of 400 the real provider raised. Records what it was sent."""

    def __init__(self):
        self.received_data_urls = []

    async def generate_with_images(self, model, prompt, image_data_urls, temperature=0.0, **kw):
        self.received_data_urls = list(image_data_urls)
        for du in image_data_urls:
            b64 = du.split(",", 1)[1]
            raw = base64.b64decode(b64)
            if not _is_decodable_image(raw):
                raise RuntimeError(
                    "Error code: 400 - invalid_image_format: You uploaded an "
                    "unsupported image. Please make sure your image has one of "
                    "the following formats: ['jpeg', 'webp', 'gif', 'png']."
                )
        return (
            '{"text_legible": true, "text_coherent": true, '
            '"matches_intended_content": true, "specific_issues": []}'
        )


# ── [1] necessity: raw PDF bytes are not an image; the helper fixes that ─────
print("[1] raw PDF bytes are undecodable (what the provider rejected); helper yields decodable PNG...")

with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / "design.pdf"
    _make_pdf(pdf)

    raw_pdf_decodable = _is_decodable_image(pdf.read_bytes())
    helper_bytes = _delivery_image_bytes(pdf)
    helper_decodable = _is_decodable_image(helper_bytes)

    if not raw_pdf_decodable and helper_decodable:
        ok("[1] raw PDF bytes rejected by image decode; _delivery_image_bytes(pdf) -> decodable PNG")
    else:
        fail("[1] necessity", f"raw_pdf_decodable={raw_pdf_decodable}, helper_decodable={helper_decodable}")


# ── [2] consistency gate on a PDF delivery: only images sent, gate passes ────
print("[2] check_marketing_consistency(pdf, [png]) -> vision model gets only images, passes...")

with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / "design.pdf"
    marketing = Path(tmp) / "hero.png"
    _make_pdf(pdf)
    _make_png(marketing)

    provider = FakeVisionProvider()
    svc = ContentQualityService(provider=provider)
    result = svc.check_marketing_consistency(pdf, [marketing], "Mindfulness Daily Planner")

    all_images = bool(provider.received_data_urls) and all(
        _is_decodable_image(base64.b64decode(du.split(",", 1)[1])) for du in provider.received_data_urls
    )
    if result.passed and len(provider.received_data_urls) == 2 and all_images:
        ok("[2] PDF delivery + marketing PNG both sent as decodable images; consistency gate passed")
    else:
        fail("[2] consistency on PDF", f"passed={result.passed}, n={len(provider.received_data_urls)}, all_images={all_images}, issues={result.specific_issues}")


# ── [3] regression: single-image (PNG) delivery still works ──────────────────
print("[3] single-image PNG delivery still handled unchanged...")

with tempfile.TemporaryDirectory() as tmp:
    design = Path(tmp) / "design.png"
    marketing = Path(tmp) / "hero.png"
    _make_png(design)
    _make_png(marketing)

    provider = FakeVisionProvider()
    svc = ContentQualityService(provider=provider)
    result = svc.check_marketing_consistency(design, [marketing], "Botanical Line Art Print")

    if result.passed and _delivery_image_bytes(design) == design.read_bytes():
        ok("[3] PNG delivery bytes passed through untouched; gate passed")
    else:
        fail("[3] png regression", f"passed={result.passed}, issues={result.specific_issues}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")
sys.exit(0 if _failed == 0 else 1)
