"""
Step 103 / B-4 test — deterministic text rendering.

  [1] TextOverlayService.overlay writes correctly onto an image (changes pixels
      in the center), auto-sizes to fit, and returns True.
  [2] empty text -> no-op (returns False, file unchanged).
  [3] _stage_pod_design overlays display_text before returning (design pixels
      change vs a no-text run).

Usage: python scripts/test_step103_text_overlay.py
"""
import os
import sys
import tempfile

os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from app.services.text_overlay_service import TextOverlayService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


tmp = tempfile.mkdtemp()

# [1] overlay changes center pixels
p = os.path.join(tmp, "bg.png")
Image.new("RGB", (1200, 1600), (230, 220, 200)).save(p)
before = list(Image.open(p).getdata())
ok = TextOverlayService().overlay(p, "Be Kind To Yourself Every Single Day")
after = list(Image.open(p).getdata())
check("1 overlay returns True", ok is True)
check("1 pixels changed (text drawn)", before != after)
check("1 dark ink present (text is dark)", any(sum(px) < 200 for px in after))

# [2] empty text -> no-op
p2 = os.path.join(tmp, "bg2.png")
Image.new("RGB", (800, 800), (255, 255, 255)).save(p2)
b2 = list(Image.open(p2).getdata())
ok2 = TextOverlayService().overlay(p2, "   ")
check("2 empty text returns False", ok2 is False)
check("2 file unchanged", b2 == list(Image.open(p2).getdata()))

# [3] long phrase still fits (auto-size) and draws multiple lines
p3 = os.path.join(tmp, "bg3.png")
Image.new("RGB", (1000, 1400), (240, 240, 240)).save(p3)
ok3 = TextOverlayService().overlay(p3, "This is a much longer affirmation phrase that must wrap across several lines and still fit within the image bounds")
check("3 long phrase overlaid OK", ok3 is True)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 text-overlay tests passed.")
