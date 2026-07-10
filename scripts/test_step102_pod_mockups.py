"""
Step 102 / P1-1 test — POD listing photos come from the REAL Printify mockups.

  [1] _stage_pod_listing_images downloads Printify mockup images, validates and
      registers them as listing photos (agent=PrintifyMockup), preferring the
      default/publishing-selected ones.
  [2] returns [] when the product has no mockup images (caller falls back).
  [3] returns [] when the pod product has no printify_product_id.

The fallback path (mockups unavailable -> independent generation + consistency
gate) is exercised end-to-end by test_step100b_consistency_remake, whose mock
pod has no printify_product_id and so naturally takes the fallback.

Usage: python scripts/test_step102_pod_mockups.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pipeline_orchestrator import PipelineOrchestrator

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def run(product_images, printify_product_id="prod-1"):
    orch = PipelineOrchestrator()
    orch.catalog = MagicMock()
    report = {"stages": {}}

    printify = MagicMock()
    printify.get_product.return_value = {"images": product_images}

    fs = MagicMock()
    fs.save_bytes.side_effect = lambda content, tid, variant, fname: f"/img/{tid}/{fname}"

    validator = MagicMock()

    resp = MagicMock()
    resp.content = b"PNGBYTES"
    resp.raise_for_status.return_value = None

    pod = SimpleNamespace(printify_product_id=printify_product_id)

    with patch("app.services.printify_client.PrintifyClient", return_value=printify), \
         patch("app.services.image_file_service.ImageFileService", return_value=fs), \
         patch("app.services.pipeline_orchestrator.ImageValidationService", return_value=validator), \
         patch("httpx.get", return_value=resp):
        paths = orch._stage_pod_listing_images("task-1", pod, report)
    return paths, report, printify, orch


# [1] mockups downloaded + registered, default preferred
images = [
    {"src": "http://x/plain.png", "is_default": False, "is_selected_for_publishing": False},
    {"src": "http://x/default.png", "is_default": True, "is_selected_for_publishing": True},
    {"src": "http://x/second.png", "is_default": False, "is_selected_for_publishing": True},
]
paths, report, printify, orch = run(images)
check("1 returns mockup paths", len(paths) == 3)
check("1 hero is the default mockup (sorted first)", str(paths[0]).endswith("hero.png"))
check("1 registered as PrintifyMockup", orch.catalog.register.call_args.kwargs.get("agent") == "PrintifyMockup")
check("1 report source is printify_mockup", report["stages"]["listing_images"]["source"] == "printify_mockup")

# [2] no images -> [] (fallback)
paths2, report2, _, _ = run([])
check("2 no images -> empty (fallback)", paths2 == [])
check("2 report marks failure", report2["stages"]["listing_images"]["ok"] is False)

# [3] no printify_product_id -> [] (fallback)
paths3, report3, printify3, _ = run(images, printify_product_id=None)
check("3 missing printify_product_id -> empty", paths3 == [])
check("3 get_product NOT called without an id", not printify3.get_product.called)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 POD-mockup tests passed.")
