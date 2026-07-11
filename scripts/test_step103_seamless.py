"""
Step 103 / B-3 test — seamless_pattern format + seamlessness check.

  [1] seamless_pattern is a registered single-image digital format with a real
      taxonomy_id and materials.
  [2] edge_mismatch/is_seamless: a tiling image passes, a hard-split image fails.

Usage: python scripts/test_step103_seamless.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw
from app.core.product_formats import PRODUCT_FORMATS, materials_for, description_blocks
from app.core.seamless import edge_mismatch, is_seamless

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] format registered
spec = PRODUCT_FORMATS.get("seamless_pattern")
check("1 seamless_pattern registered", spec is not None)
check("1 single-image digital", spec and spec["category"] == "digital" and spec["delivery"] == "single_image")
check("1 has int taxonomy_id != 1", isinstance(spec.get("taxonomy_id"), int) and spec["taxonomy_id"] != 1)
check("1 materials set", "seamless pattern" in " ".join(materials_for("seamless_pattern")))
check("1 description mentions seamless", "SEAMLESS" in description_blocks("seamless_pattern"))

# [2] seamlessness check
tmp = tempfile.mkdtemp()
# a gentle horizontal gradient that wraps would be ~seamless; use a uniform tile
uni = os.path.join(tmp, "u.png")
Image.new("RGB", (500, 500), (100, 140, 90)).save(uni)
check("2 uniform tile is seamless", is_seamless(uni) and edge_mismatch(uni) < 5)

split = os.path.join(tmp, "s.png")
im = Image.new("RGB", (500, 500), (255, 255, 255))
ImageDraw.Draw(im).rectangle([250, 0, 500, 500], fill=(0, 0, 0))  # left white / right black
im.save(split)
check("2 hard-split image is NOT seamless", not is_seamless(split))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 seamless tests passed.")
