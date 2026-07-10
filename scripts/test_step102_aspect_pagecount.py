"""
Step 102 / P1-2 + P1-6 test — per-format delivery aspect ratios and honest
PDF page-count in the description.

P1-2:
  [1] delivery_aspect_for returns the right shape per format (9:16 wallpaper,
      3:4 card/coloring, 1:1 print/sticker/pod).
  [2] ImageValidationService.validate accepts an expected_ratio override: a 9:16
      image PASSES delivery validation with (9,16) but FAILS against default 1:1.

P1-6:
  [3] a description claiming "5-page" / "5 pages" is rewritten to the REAL
      page count and gets an explicit "Includes N printable pages." line.

Usage: python scripts/test_step102_aspect_pagecount.py
"""
import os
import re
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image as PILImage
from app.core.product_formats import delivery_aspect_for, aspect_to_ratio
from app.services.image_validation_service import ImageValidationService, ImageValidationError

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] aspects
check("1 phone_wallpaper 9:16", delivery_aspect_for("phone_wallpaper") == "9:16")
check("1 greeting_card 3:4", delivery_aspect_for("greeting_card_design") == "3:4")
check("1 coloring_page 3:4", delivery_aspect_for("coloring_page") == "3:4")
check("1 single_print 1:1", delivery_aspect_for("single_print") == "1:1")
check("1 sticker_sheet 1:1", delivery_aspect_for("sticker_sheet_design") == "1:1")
check("1 pod 1:1", delivery_aspect_for("pod_apparel_design") == "1:1")
check("1 aspect_to_ratio parses 9:16", aspect_to_ratio("9:16") == (9, 16))

# [2] validation with expected_ratio override
tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "wallpaper.png")
PILImage.new("RGB", (2304, 4096), (255, 255, 255)).save(p)  # 9:16, both edges > 1000
from pathlib import Path
ok_916 = True
try:
    ImageValidationService().validate(Path(p), use_case="delivery", expected_ratio=(9, 16))
except ImageValidationError as e:
    ok_916 = False
    print("   9:16 validation error:", e)
check("2 9:16 image passes delivery with expected_ratio=(9,16)", ok_916)

rejected_default = False
try:
    ImageValidationService().validate(Path(p), use_case="delivery")  # default 1:1
except ImageValidationError:
    rejected_default = True
check("2 same 9:16 image FAILS against default 1:1 rule", rejected_default)

# [3] page-count reconciliation (mirror the orchestrator logic)
def reconcile(desc, n):
    desc = re.sub(r"\b\d+(?=[\s-]?pages?\b)", str(n), desc, flags=re.I)
    if "printable page" not in desc.lower():
        desc = desc.rstrip() + f"\n\nIncludes {n} printable pages."
    return desc

out = reconcile("This 5-page planner keeps you organized. A 5 pages set.", 4)
check("3 '5-page' rewritten to real count", "4-page" in out and "5-page" not in out)
check("3 '5 pages' rewritten to real count", "4 pages" in out)
check("3 explicit includes line appended", "Includes 4 printable pages." in out)
# idempotent-ish: if already mentions printable pages, don't double-append
out2 = reconcile("A great planner. Includes 4 printable pages.", 4)
check("3 no duplicate includes line", out2.lower().count("printable page") == 1)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 aspect+page-count tests passed.")
