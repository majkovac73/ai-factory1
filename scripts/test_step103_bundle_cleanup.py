"""
Step 103 / A-5 + A-8 + disk-hygiene test.

A-5 (delivery bundles):
  [1] single_print master -> multiple print-ratio files (<=5), master included.
  [2] phone_wallpaper -> device-size variants.
  [3] coloring_page -> original + letter PDF.
  [4] other formats -> master only.
  [5] size_summary text per format.

Disk hygiene (ImageCleanupService):
  [6] prunes old listing/delivery files, keeps recent ones and the scenes cache.

Usage: python scripts/test_step103_bundle_cleanup.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(_tmp, "images")  # data dir = _tmp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from app.services.delivery_bundle_service import DeliveryBundleService
from app.services.image_cleanup_service import ImageCleanupService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def make_master(name, size=(2000, 3000)):
    d = Path(_tmp) / "masters"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    Image.new("RGB", size, (120, 160, 200)).save(p)
    return p


svc = DeliveryBundleService()

# [1] single_print
files = svc.build(make_master("sp.png"), "single_print")
check("1 single_print multi-file (>=4)", len(files) >= 4)
check("1 single_print <=5 files (Etsy limit)", len(files) <= 5)
check("1 master included first", files[0].name == "sp.png")
check("1 variants exist on disk", all(f.exists() for f in files))
# a 2:3 variant should be portrait
v23 = [f for f in files if "2x3" in f.name][0]
w, h = Image.open(v23).size
check("1 2x3 variant is portrait ratio", abs((w / h) - (2 / 3)) < 0.02)

# [2] phone_wallpaper
pf = svc.build(make_master("pw.png", size=(1170, 2532)), "phone_wallpaper")
check("2 phone bundle multi-file", len(pf) >= 2)

# [3] coloring_page -> pdf
cp = svc.build(make_master("cp.png"), "coloring_page")
check("3 coloring bundle has a PDF", any(f.suffix == ".pdf" for f in cp))

# [4] B-2: greeting card -> original + half-fold card PDF
gc = svc.build(make_master("gc.png"), "greeting_card_design")
check("4 greeting card bundle has 2 files", len(gc) == 2)
check("4 greeting card includes a fold-over PDF", any(f.suffix == ".pdf" for f in gc))
# a truly single-file format (sticker) still returns master only
st = svc.build(make_master("st.png"), "sticker_sheet_design")
check("4 sticker sheet single file", len(st) == 1)

# [5] size summary
check("5 single_print summary mentions ratios", "ratios" in DeliveryBundleService.size_summary("single_print", 5))
check("5 coloring summary mentions PDF", "PDF" in DeliveryBundleService.size_summary("coloring_page", 2))
check("5 no summary for single file", DeliveryBundleService.size_summary("single_print", 1) == "")

# [6] cleanup
images = Path(_tmp) / "images"
for sub in ("listing/t1", "delivery/t1", "scenes"):
    (images / sub).mkdir(parents=True, exist_ok=True)
old_listing = images / "listing/t1/old.png"; old_listing.write_bytes(b"x")
new_listing = images / "listing/t1/new.png"; new_listing.write_bytes(b"x")
old_delivery = images / "delivery/t1/old.png"; old_delivery.write_bytes(b"x")
scene = images / "scenes/framed_0.png"; scene.write_bytes(b"x")
# age the "old" files well beyond thresholds
old_ts = time.time() - 100 * 3600
os.utime(old_listing, (old_ts, old_ts))
os.utime(old_delivery, (old_ts, old_ts))
old_scene_ts = time.time() - 1000 * 3600
os.utime(scene, (old_scene_ts, old_scene_ts))

ImageCleanupService().cleanup()
check("6 old listing mockup pruned", not old_listing.exists())
check("6 recent listing mockup kept", new_listing.exists())
check("6 old delivery file pruned", not old_delivery.exists())
check("6 scenes cache preserved", scene.exists())

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 bundle+cleanup tests passed.")
