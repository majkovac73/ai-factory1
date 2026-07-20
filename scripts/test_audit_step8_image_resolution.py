"""
Audit 2026-07-20 #8 — listing photos >= 2000px, landscape hero.

Verifies MockupService composites at the configured resolution (not 1024) and
that a landscape hero (width != height) renders without the old square-only math
breaking. Uses the deterministic PIL fallback (no image API).

Usage: python scripts/test_audit_step8_image_resolution.py
"""
import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from PIL import Image

from app.services.mockup_service import MockupService
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# a simple design file
tmp = tempfile.mkdtemp()
design = os.path.join(tmp, "design.png")
Image.new("RGB", (1500, 1500), (120, 160, 200)).save(design)

svc = MockupService()

with patch.object(settings, "MOCKUP_USE_GENERATED_SCENES", False):
    # square mockup at LISTING_IMAGE_SIZE (>=2000)
    sq = svc.build_mockup_bytes(design, role="framed")
    img = Image.open(BytesIO(sq))
    check("square mockup >= 2000px on both sides", img.width >= 2000 and img.height >= 2000)
    check("square mockup is square", img.width == img.height)

    # landscape hero
    hero = svc.build_mockup_bytes(design, role="framed",
                                  size=settings.LISTING_HERO_W, height=settings.LISTING_HERO_H)
    himg = Image.open(BytesIO(hero))
    check("hero width == LISTING_HERO_W", himg.width == settings.LISTING_HERO_W)
    check("hero height == LISTING_HERO_H", himg.height == settings.LISTING_HERO_H)
    check("hero is landscape (w > h)", himg.width > himg.height)
    check("hero shortest side >= 1600", min(himg.width, himg.height) >= 1600)

    # flatlay too
    fl = svc.build_mockup_bytes(design, role="flatlay")
    fimg = Image.open(BytesIO(fl))
    check("flatlay >= 2000px", min(fimg.width, fimg.height) >= 2000)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#8 image-resolution tests passed.")
