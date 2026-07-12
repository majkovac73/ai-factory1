"""
Step 105-A test — 1-3 distinct set pieces, 1-4 text-led regen keeps overlay,
1-5 coloring-page whiteness check.

Usage: python scripts/test_step105_pipeline_correctness.py
"""
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s105a.db")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 1-3: import os present; set pieces are DISTINCT files ──
import app.services.pipeline_orchestrator as po
check("1-3 pipeline_orchestrator imports os", hasattr(po, "os"))

from app.services.pipeline_orchestrator import PipelineOrchestrator
orch = PipelineOrchestrator()

tmp = tempfile.mkdtemp()
# stub PODPipelineService.build_product_record to honor the `filename` arg and
# write a DISTINCT-colored image per piece (proves no clobber).
colors = {"set_piece_1.png": (200, 60, 60), "set_piece_2.png": (60, 200, 60), "set_piece_3.png": (60, 60, 200)}
made = []


def fake_build(task_id, product_name, visual_brief, product_type="digital_download",
               aspect_ratio="1:1", resolution="2K", filename="design.png"):
    p = os.path.join(tmp, filename)
    Image.new("RGB", (256, 256), colors.get(filename, (128, 128, 128))).save(p)
    made.append(p)
    return {"design_path": p, "ready_for_pod": True}


fake_pod = MagicMock()
fake_pod.build_product_record.side_effect = fake_build
cq = MagicMock()
cq.review_asset_file.return_value = MagicMock(passed=True, specific_issues=[])
with patch("app.services.pipeline_orchestrator.PODPipelineService", return_value=fake_pod), \
     patch("app.services.content_quality_service.ContentQualityService", return_value=cq), \
     patch.object(orch, "catalog", MagicMock()), \
     patch.object(orch, "_build_listing_mockups", return_value=[]), \
     patch("app.services.image_validation_service.ImageValidationService"), \
     patch("app.services.delivery_bundle_service.DeliveryBundleService"):
    report = {"stages": {}}
    res = orch._stage_wall_art_set("taskset", "Desert Trio", "warm desert theme", report)

piece_paths = [str(p) for p in (res or {}).get("pieces", [])]
check("1-3 three pieces produced", len(piece_paths) == 3)
check("1-3 piece paths are distinct", len(set(piece_paths)) == 3)
# distinct CONTENT (hash) — the real bug was identical images
import hashlib
hashes = set()
for p in piece_paths:
    with open(p, "rb") as f:
        hashes.add(hashlib.sha256(f.read()).hexdigest())
check("1-3 piece file contents are distinct (not 3 copies)", len(hashes) == 3)

# ── 1-4: text-led regen re-passes display_text + no-text brief ──
regen_calls = []


def fake_pod_design(task_id, product_name, brief, task_type, report, display_text=None):
    regen_calls.append({"brief": brief, "display_text": display_text})
    return "regenerated.png"


# first review fails, second passes → forces exactly one regen
review = MagicMock()
review.side_effect = [MagicMock(passed=False, specific_issues=["garbled"]),
                      MagicMock(passed=True, specific_issues=[])]
svc = MagicMock()
svc.review_asset_file = review
with patch("app.services.content_quality_service.ContentQualityService", return_value=svc), \
     patch.object(orch, "_stage_pod_design", side_effect=fake_pod_design), \
     patch("config.settings.CONTENT_QA_MAX_ATTEMPTS", 3):
    rep = {"stages": {}}
    out = orch._stage_content_quality(
        "t2", "orig.png", "Be Kind Print", "affirmation art", "single_print", rep,
        design_brief="affirmation art. IMPORTANT: create a purely DECORATIVE background with NO text",
        display_text="Be Kind")

check("1-4 regen happened once", len(regen_calls) == 1)
check("1-4 regen received display_text", regen_calls[0]["display_text"] == "Be Kind")
check("1-4 regen used the no-text brief", "NO text" in regen_calls[0]["brief"])

# ── 1-5: coloring-page whiteness check ──
from app.core.coloring_page import color_fraction, is_uncolored

# clean line art: white page with thin black lines
line = Image.new("RGB", (300, 300), (255, 255, 255))
lp = line.load()
for x in range(300):
    lp[x, 150] = (0, 0, 0)
    lp[150, x] = (0, 0, 0)
clean_path = os.path.join(tmp, "clean.png")
line.save(clean_path)

# half-colored page: a big colored block
colored = Image.new("RGB", (300, 300), (255, 255, 255))
for y in range(0, 150):
    for x in range(300):
        colored.load()[x, y] = (240, 120, 40)
colored_path = os.path.join(tmp, "colored.png")
colored.save(colored_path)

check("1-5 clean line art is uncolored", is_uncolored(clean_path) is True)
check("1-5 clean line art color fraction is tiny", color_fraction(clean_path) < 0.03)
check("1-5 half-colored page flagged colored", is_uncolored(colored_path) is False)
check("1-5 half-colored fraction is large", color_fraction(colored_path) > 0.3)

# integration: coloring_page that stays colored blocks after retries
made_designs = ["c1.png", "c2.png"]
Image.new("RGB", (100, 100), (240, 120, 40)).save(os.path.join(tmp, "always_colored.png"))


def always_colored(task_id, product_name, brief, task_type, report, display_text=None):
    return os.path.join(tmp, "always_colored.png")


svc2 = MagicMock()
svc2.review_asset_file.return_value = MagicMock(passed=True, specific_issues=[])
with patch("app.services.content_quality_service.ContentQualityService", return_value=svc2), \
     patch.object(orch, "_stage_pod_design", side_effect=always_colored), \
     patch.object(orch, "_block_task") as block, \
     patch("config.settings.CONTENT_QA_MAX_ATTEMPTS", 2):
    rep2 = {"stages": {}}
    out2 = orch._stage_content_quality("t3", os.path.join(tmp, "always_colored.png"),
                                       "Cat Coloring Page", "line art cat", "coloring_page", rep2)
check("1-5 persistently-colored coloring page is blocked", out2 is None and block.called)
check("1-5 vision review skipped for colored page", not svc2.review_asset_file.called)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-105-A tests passed.")
