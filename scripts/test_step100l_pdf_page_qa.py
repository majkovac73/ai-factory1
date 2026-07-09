"""
Step 100l test — stricter per-page QA for PDF planners/guides.

Live on a real PDF (task 3000c3a9): generated planner pages had Seedream quirks
the generic per-page QA missed — a decorative PHOTO (a pineapple) on a meal-
planner page and GARBLED meta-text ("Print-Iready ... Design Page 4 of 6"). Fix:
(a) the generation prompt now hard-steers to a clean functional layout with no
imagery / no meta-text, and (b) a dedicated STRICT reviewer
(ContentQualityService.review_pdf_page_bytes) rejects photographs, garbled text,
and stray meta-text; PDFGenerationService uses it per page.

Tests (fake vision provider — no real API):
  [1] review_pdf_page_bytes: FAILS a page the model flags (photo / garbled text);
      PASSES a clean page.
  [2] The strict review PROMPT names the right things to reject (photographs,
      garbled/misspelled text, meta-text like 'page x of y') and demands a clean
      functional layout.
  [3] The GENERATION prompt forbids photographs/decorative imagery, page numbers /
      'print-ready' meta-text, and asks for a functional print-ready layout.
  [4] PDFGenerationService uses the STRICT reviewer per page (not the generic
      asset review) when it's available.

Usage:
  python scripts/test_step100l_pdf_page_qa.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import logging
logging.basicConfig(level=logging.ERROR)

from app.services.content_quality_service import ContentQualityService
from app.services.pdf_generation_service import PDFGenerationService

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 100l — strict PDF page QA\n")


class FakeVision:
    def __init__(self, verdicts):
        self._v = list(verdicts)
        self.calls = 0

    async def generate_with_images(self, model, prompt, image_data_urls, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return json.dumps(self._v.pop(0))


BAD_PHOTO = {"text_legible": True, "text_coherent": True, "matches_intended_content": False,
             "specific_issues": ["photo of a pineapple on a meal-planner page"]}
BAD_GARBLED = {"text_legible": True, "text_coherent": False, "matches_intended_content": True,
               "specific_issues": ["garbled word: 'Print-Iready'"]}
CLEAN = {"text_legible": True, "text_coherent": True, "matches_intended_content": True, "specific_issues": []}


# ── [1] strict reviewer fails photo/garbled, passes clean ────────────────────
print("[1] review_pdf_page_bytes fails a photo/garbled page and passes a clean one...")

svc = ContentQualityService(provider=FakeVision([BAD_PHOTO, BAD_GARBLED, CLEAN]), model="fake")
r_photo = svc.review_pdf_page_bytes(b"x", "Meal Planner", "Page 1: weekly meals table")
r_garbled = svc.review_pdf_page_bytes(b"x", "Meal Planner", "Page 4: snacks table")
r_clean = svc.review_pdf_page_bytes(b"x", "Meal Planner", "Page 2: grocery list")
if r_photo.passed is False and r_garbled.passed is False and r_clean.passed is True:
    ok("[1] photo page + garbled page rejected; clean page passes")
else:
    fail("[1] strict review", f"photo={r_photo.passed}, garbled={r_garbled.passed}, clean={r_clean.passed}")


# ── [2] strict review prompt names the right rejections ──────────────────────
print("[2] strict review prompt targets photos / garbled text / meta-text / functional layout...")

p = ContentQualityService(provider=FakeVision([]), model="fake")._build_pdf_page_review_prompt("Meal Planner", "Page 4: snacks").lower()
need = ["photograph", "garbled", "misspelled", "page 4 of 6", "functional layout", "print-ready"]
missing = [t for t in need if t not in p]
if not missing:
    ok("[2] strict review prompt covers photos, garbled/misspelled text, meta-text, and functional layout")
else:
    fail("[2] review prompt", f"missing: {missing}")


# ── [3] generation prompt forbids imagery + meta-text ────────────────────────
print("[3] generation prompt forbids photographs/imagery + page numbers/meta-text, asks functional layout...")

gp = PDFGenerationService(image_provider=object(), content_quality_service=object())._build_page_prompt(
    "Meal Planner", "minimalist", "weekly meals table", 4, 6
).lower()
gen_need = ["do not include any photograph", "page numbers", "print-ready", "functional layout", "white background"]
gen_missing = [t for t in gen_need if t not in gp]
# also must NOT print "page 4 of 6" style meta-text into the generation prompt output
if not gen_missing:
    ok("[3] generation prompt forbids imagery + meta-text and requests a clean functional print-ready layout")
else:
    fail("[3] generation prompt", f"missing: {gen_missing}")


# ── [4] PDFGenerationService uses the strict reviewer per page ───────────────
print("[4] PDFGenerationService calls review_pdf_page_bytes (strict) per page...")


class SpyQA:
    def __init__(self): self.strict = 0; self.generic = 0
    def review_pdf_page_bytes(self, image_bytes, product_name, page_desc=""):
        self.strict += 1
        import types
        return types.SimpleNamespace(passed=True, specific_issues=[])
    def review_asset_bytes(self, *a, **k):
        self.generic += 1
        import types
        return types.SimpleNamespace(passed=True, specific_issues=[])


from PIL import Image as _Img
from io import BytesIO as _BIO


class _FakeImgProvider:
    async def generate_image(self, prompt, aspect_ratio=None, resolution=None):
        buf = _BIO(); _Img.new("RGB", (1024, 1536), (255, 255, 255)).save(buf, "PNG")
        import base64, types
        return types.SimpleNamespace(b64_data=base64.b64encode(buf.getvalue()).decode(), url=None)


spy = SpyQA()
import tempfile
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
svc4 = PDFGenerationService(image_provider=_FakeImgProvider(), content_quality_service=spy)
try:
    svc4.generate_pdf("task-100l", "Meal Planner", "minimalist", ["Page 1", "Page 2", "Page 3"])
    used_strict = spy.strict == 3 and spy.generic == 0
except Exception as e:
    used_strict = False
    print("   (generate_pdf raised:", e, ")")
if used_strict:
    ok("[4] the strict per-page reviewer was used for every page (generic review not used)")
else:
    fail("[4] strict used", f"strict={spy.strict}, generic={spy.generic}")


print(f"\nResults: {_passed} passed, {_failed} failed\n")
sys.exit(0 if _failed == 0 else 1)
