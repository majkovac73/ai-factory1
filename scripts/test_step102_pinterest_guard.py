"""
Step 102 / P0-6 + P1-5 (#1c,#5) test — Pinterest stage skips BEFORE spending on
a pin image when Pinterest CANNOT PUBLISH.

The bug: _stage_pinterest generated a ~$0.04 pin image via
PinterestImageService.enrich_listing_with_image and only then tried to post —
so every task wasted an image call while Pinterest is inactive (no token) OR
Trial-blocked (403 code 29 on every pin-create). The gate is now
pinterest_oauth.can_publish() (real publish capability), not merely is_connected().

Tests (no real API/image calls):
  [1] cannot publish -> stage skipped, enrich_listing_with_image NEVER called.
  [2] can publish -> enrich IS called (guard doesn't block the real path).
  [3] can_publish() probe: explicit override, Trial-403 history, success history.

Usage: python scripts/test_step102_pinterest_guard.py
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services import pinterest_oauth

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def run_stage(can_publish: bool):
    orch = PipelineOrchestrator()
    report = {"stages": {}}
    pis = MagicMock()
    pis.return_value.enrich_listing_with_image.return_value = {"pin_image_path": "/tmp/x.png"}
    ms = MagicMock()
    ms.return_value.get_posts_for_task.return_value = []
    ms.return_value.post_to_channel.return_value = {"success": True}
    with patch("app.services.pinterest_oauth.can_publish", return_value=can_publish), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms), \
         patch.object(orch, "catalog", MagicMock()):
        orch._stage_pinterest("task-1", "Test Product", "brief", {"title": "t"}, report)
    return report, pis


# [1] cannot publish -> skipped, no image generated
report, pis = run_stage(can_publish=False)
check("1 stage skipped when cannot publish",
      "skipped" in report["stages"].get("pinterest", {}))
check("1 enrich_listing_with_image NEVER called (no spend)",
      not pis.return_value.enrich_listing_with_image.called)

# [2] can publish -> real path runs (enrich called)
report2, pis2 = run_stage(can_publish=True)
check("2 enrich called when can publish",
      pis2.return_value.enrich_listing_with_image.called)


# [3] can_publish() probe logic ---------------------------------------------
class _Post:
    def __init__(self, status, error_message=None):
        self.status = status
        self.error_message = error_message
        self.channel = "pinterest"


def probe_with(recent_posts, override, connected=True):
    class _Q:
        def filter(self, *a, **k): return self
        def order_by(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def all(self): return recent_posts
    class _DB:
        def query(self, *a, **k): return _Q()
        def close(self): pass
    with patch.object(pinterest_oauth, "is_connected", return_value=connected), \
         patch.object(pinterest_oauth.settings, "PINTEREST_CAN_PUBLISH", override, create=True), \
         patch.object(pinterest_oauth, "SessionLocal", lambda: _DB()):
        return pinterest_oauth.can_publish()


check("3 not connected -> False", probe_with([], None, connected=False) is False)
check("3 explicit override True -> True", probe_with([], True) is True)
check("3 explicit override False -> False", probe_with([], False) is False)
check("3 recent Trial-403 failure -> False",
      probe_with([_Post("failed", '403: {"code":29,"message":"Apps with Trial access may not create Pins in production"}')], None) is False)
check("3 recent success -> True",
      probe_with([_Post("success")], None) is True)
check("3 no decisive history -> True (optimistic bootstrap)",
      probe_with([], None) is True)
check("3 success newer than 403 (success wins, ordered desc) -> True",
      probe_with([_Post("success"), _Post("failed", "trial access")], None) is True)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 Pinterest-guard tests passed.")
