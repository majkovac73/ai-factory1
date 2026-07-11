"""
Step 103 / A-6 test — deterministic planner page rendering.

  [1] derive_spec picks the right layout from a page brief's keywords.
  [2] render produces a valid non-blank portrait page image per layout.
  [3] generate_pdf with render_interior=True: interior pages are RENDERED
      (only the cover hits the image provider), and a 20-page planner assembles
      and reads back — proving MAX_PDF_PAGES can be well above 6 at ~1 image call.

Usage: python scripts/test_step103_planner_render.py
"""
import base64
import os
import sys
import tempfile
import types
from io import BytesIO

os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from app.services.planner_page_renderer import PlannerPageRenderer, PAGE_W, PAGE_H
from app.services.pdf_generation_service import PDFGenerationService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] layout inference
cases = {
    "Weekly meal schedule": "weekly_grid",
    "Grocery shopping checklist": "checklist",
    "Monthly budget calendar": "monthly_calendar",
    "Daily water tracker log": "tracker_table",
    "Notes and reflections": "lined",
}
for brief, expected in cases.items():
    spec = PlannerPageRenderer.derive_spec(brief)
    check(f"1 '{brief[:20]}' -> {expected}", spec["layout"] == expected)

# [2] render each layout -> non-blank portrait image
r = PlannerPageRenderer()
for layout in ("weekly_grid", "checklist", "lined", "tracker_table", "monthly_calendar", "dotted"):
    img = r.render({"heading": "Test Page", "layout": layout, "labels": ["A", "B", "C"]})
    extrema = img.convert("L").getextrema()  # (min, max) — non-blank has dark ink
    check(f"2 {layout} portrait + non-blank", img.size == (PAGE_W, PAGE_H) and extrema[0] < 100)

# [3] generate_pdf renders interiors, image-gens only the cover
gen_calls = {"n": 0}


class _CoverOnlyProvider:
    async def generate_image(self, prompt, aspect_ratio=None, resolution=None):
        gen_calls["n"] += 1
        buf = BytesIO()
        Image.new("RGB", (1024, 1536), (250, 245, 235)).save(buf, "PNG")
        return types.SimpleNamespace(b64_data=base64.b64encode(buf.getvalue()).decode(), url=None)


class _PassQA:
    def review_pdf_page_bytes(self, *a, **k):
        return types.SimpleNamespace(passed=True, specific_issues=[])


svc = PDFGenerationService(image_provider=_CoverOnlyProvider(), content_quality_service=_PassQA())
briefs = ["Cover"] + [f"Weekly plan page {i}" for i in range(19)]  # 20 pages
pdf_path = svc.generate_pdf("t-a6", "Ultimate Weekly Planner", "minimalist", briefs, render_interior=True)
check("3 20-page planner assembled", pdf_path.exists())
check("3 only the COVER hit the image provider (1 call)", gen_calls["n"] == 1)

# readback page count
from pypdf import PdfReader
check("3 pypdf readback = 20 pages", len(PdfReader(str(pdf_path)).pages) == 20)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 planner-render tests passed.")
