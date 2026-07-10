"""
Step 102 / P0-6 test — Pinterest stage skips BEFORE spending on a pin image
when Pinterest isn't connected.

The bug: _stage_pinterest generated a ~$0.04 pin image via
PinterestImageService.enrich_listing_with_image and only then tried to post —
so every task wasted an image call while Pinterest is inactive (no token).

Tests (no real API/image calls):
  [1] not connected -> stage skipped, enrich_listing_with_image NEVER called.
  [2] connected -> enrich IS called (guard doesn't block the real path).

Usage: python scripts/test_step102_pinterest_guard.py
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pipeline_orchestrator import PipelineOrchestrator

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def run_stage(connected: bool):
    orch = PipelineOrchestrator()
    report = {"stages": {}}
    pis = MagicMock()
    pis.return_value.enrich_listing_with_image.return_value = {"pin_image_path": "/tmp/x.png"}
    ms = MagicMock()
    ms.return_value.get_posts_for_task.return_value = []
    ms.return_value.post_to_channel.return_value = {"success": True}
    with patch("app.services.pinterest_oauth.is_connected", return_value=connected), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms), \
         patch.object(orch, "catalog", MagicMock()):
        orch._stage_pinterest("task-1", "Test Product", "brief", {"title": "t"}, report)
    return report, pis


# [1] not connected -> skipped, no image generated
report, pis = run_stage(connected=False)
check("1 stage skipped when not connected",
      report["stages"].get("pinterest", {}).get("skipped") == "Pinterest not connected")
check("1 enrich_listing_with_image NEVER called (no spend)",
      not pis.return_value.enrich_listing_with_image.called)

# [2] connected -> real path runs (enrich called)
report2, pis2 = run_stage(connected=True)
check("2 enrich called when connected",
      pis2.return_value.enrich_listing_with_image.called)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 Pinterest-guard tests passed.")
