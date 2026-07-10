"""
Step 102 / P1-4 + P1-5 test — PDF per-page QA fails CLOSED; assembled PDF
respects Etsy's 20MB cap.

  [1] P1-4: when page QA returns None (vision unavailable), generate_pdf RAISES
      PDFGenerationError instead of shipping the unreviewed page.
  [2] P1-4: a QA double that RAISES also results in a blocked PDF.
  [3] P1-5: _scale_for_pdf downscales a 4K page to the print-quality long edge.
  [4] P1-5: a normal small PDF assembles under the byte cap; with an
      artificially tiny cap, assembly RAISES rather than shipping an oversized file.

Usage: python scripts/test_step102_pdf_qa_size.py
"""
import base64
import os
import sys
import tempfile
import types
from io import BytesIO

os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image as PILImage
from app.services.pdf_generation_service import PDFGenerationService, PDFGenerationError

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


class _FakeImgProvider:
    async def generate_image(self, prompt, aspect_ratio=None, resolution=None):
        buf = BytesIO()
        PILImage.new("RGB", (1024, 1536), (255, 255, 255)).save(buf, "PNG")
        return types.SimpleNamespace(b64_data=base64.b64encode(buf.getvalue()).decode(), url=None)


class NoneQA:  # simulates vision unavailable
    def review_pdf_page_bytes(self, *a, **k):
        return None


class RaisingQA:
    def review_pdf_page_bytes(self, *a, **k):
        raise RuntimeError("vision API down")


# [1] None QA -> blocked
svc = PDFGenerationService(image_provider=_FakeImgProvider(), content_quality_service=NoneQA())
raised = False
try:
    svc.generate_pdf("t-none", "Meal Planner", "minimal", ["Page 1", "Page 2"])
except PDFGenerationError:
    raised = True
check("1 None QA -> PDFGenerationError (fail closed)", raised)

# [2] raising QA -> blocked (raise inside _review_page becomes None -> fail closed)
svc2 = PDFGenerationService(image_provider=_FakeImgProvider(), content_quality_service=RaisingQA())
raised2 = False
try:
    svc2.generate_pdf("t-raise", "Meal Planner", "minimal", ["Page 1"])
except PDFGenerationError:
    raised2 = True
check("2 raising QA -> PDFGenerationError (fail closed)", raised2)

# [3] scaling
svc3 = PDFGenerationService(image_provider=object(), content_quality_service=object())
big = PILImage.new("RGB", (2732, 4096), (255, 255, 255))
scaled = svc3._scale_for_pdf(big)
check("3 4K page downscaled to <= 2200 long edge", max(scaled.size) <= svc3._PDF_LONG_EDGE_PX)
check("3 aspect ratio preserved", abs((scaled.width / scaled.height) - (2732 / 4096)) < 0.01)

# [4] assembly under cap; tiny cap -> raise
pages = [PILImage.new("RGB", (1024, 1536), (255, 255, 255)) for _ in range(3)]
data = svc3._assemble_pdf_bytes(pages)
check("4 normal PDF assembles under the 19MB cap", 0 < len(data) <= svc3._ETSY_MAX_PDF_BYTES)

svc3._ETSY_MAX_PDF_BYTES = 10  # impossible to meet
raised4 = False
try:
    svc3._assemble_pdf_bytes(pages)
except PDFGenerationError:
    raised4 = True
check("4 oversized PDF (tiny cap) raises instead of shipping", raised4)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 PDF QA+size tests passed.")
