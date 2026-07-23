"""
Regression: every Pinterest pin from the main pipeline MUST carry a link back to
its Etsy listing. A linkless pin is a traffic dead end — this bug meant 50+
pins/week drove ZERO clicks to the shop (confirmed live: pin link == None).

Usage: python scripts/test_pinterest_pin_link.py
"""
import os, sys, tempfile
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pinlink.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.services.pipeline_orchestrator import PipelineOrchestrator

# a real (tiny) file for the pin-image read in the mockup branch
png = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
png.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
png.close()


def run_stage(listing_id):
    w = PipelineOrchestrator.__new__(PipelineOrchestrator)
    w.catalog = MagicMock()
    w._pin_from_mockup = lambda tid: png.name      # use mockup branch, no image gen
    w._alert = lambda *a, **k: None

    captured = {}
    fake_ms = MagicMock()
    fake_ms.get_posts_for_task.return_value = []
    def _post(task_id, listing, channel):
        captured["listing"] = listing
        return {"success": True}
    fake_ms.post_to_channel.side_effect = _post

    with patch("app.services.pipeline_orchestrator.MarketingService", return_value=fake_ms), \
         patch("app.services.pinterest_oauth.can_publish", return_value=True), \
         patch("app.services.pipeline_orchestrator.PinterestChannel", MagicMock()):
        report = {"stages": {}}
        w._stage_pinterest(
            "task-1", "Sober Anniversary Card", "brief",
            {"title": "T", "description": "D", "keywords": ["a", "b"]},
            report, task_type="greeting_card_design", listing_id=listing_id,
        )
    return captured.get("listing", {})


# 1) with a listing_id -> pin carries the correct Etsy listing URL
listing = run_stage("4542410472")
check("pin listing has listing_url set", bool(listing.get("listing_url")))
check("pin listing_url is the real Etsy listing URL",
      listing.get("listing_url") == "https://www.etsy.com/listing/4542410472")

# 2) the channel turns listing_url into the pin's `link` field (contract)
from app.marketing.pinterest_channel import PinterestChannel
import inspect
src = inspect.getsource(PinterestChannel)
check("channel maps listing_url -> pin link", 'listing.get("listing_url")' in src and '"link"' in src)

# 3) defensive: no listing_id -> empty link (never a broken/garbage URL)
listing_none = run_stage(None)
check("no listing_id -> empty link (not a malformed URL)", listing_none.get("listing_url") == "")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All Pinterest pin-link tests passed.")
