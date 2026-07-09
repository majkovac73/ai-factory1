"""
Step 100i test — the post-completion pipeline posts a NEW listing to Tumblr.

Bug: the pipeline only ever posted to Pinterest (`_stage_pinterest` hardcodes
PinterestChannel). Tumblr was only posted by the recurring marketing-refresh
worker (default OFF), so in practice a newly-created listing never got a Tumblr
post (confirmed live: task 56367aba had a failed Pinterest post and NO Tumblr
post, even though Tumblr is connected). `_stage_tumblr` now posts on creation.

Tests (doubles only — no network):
  [1] Tumblr connected + a listing asset exists -> posts to Tumblr with the
      WATERMARKED listing mockup (never the raw delivery) and the Etsy listing
      URL; result recorded.
  [2] Tumblr not connected -> skipped cleanly (no post attempted).
  [3] No listing image asset -> skipped cleanly.
  [4] Idempotent -> skipped if this task already has a successful Tumblr post.

Usage:
  python scripts/test_step100i_tumblr_on_listing.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import logging
logging.basicConfig(level=logging.ERROR)

from PIL import Image as PILImage

from app.services.pipeline_orchestrator import PipelineOrchestrator
from config import settings

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 100i — Tumblr post on new-listing creation\n")

OUTPUT = {"title": "Whimsical Owl Coloring Page", "description": "A cute printable owl coloring page.",
          "keywords": ["coloring page", "owl", "printable"]}


def _asset(path, use_case="listing"):
    return SimpleNamespace(use_case=use_case, local_path=str(path))


class FakeDB:
    def __init__(self, connected): self._c = connected
    def query(self, model): return self
    def first(self): return object() if self._c else None
    def close(self): pass


def _run_tumblr_stage(tmp, *, connected=True, listing_assets=None, existing_posts=None):
    """Invoke _stage_tumblr with doubles; return (report_stage, captured_post)."""
    orch = PipelineOrchestrator()
    orch.catalog = MagicMock()
    orch.catalog.get_listing_assets.return_value = listing_assets if listing_assets is not None else []

    captured = {}
    ms = MagicMock()
    ms.return_value.get_posts_for_task.return_value = existing_posts or []
    def _post(task_id, listing, channel):
        captured["task_id"] = task_id
        captured["listing"] = listing
        captured["channel"] = channel
        return {"success": True, "external_id": "T1", "url": "https://productsforall.tumblr.com/post/T1"}
    ms.return_value.post_to_channel.side_effect = _post

    report = {"stages": {}}
    with patch.object(settings, "TUMBLR_CONSUMER_KEY", "ck"), \
         patch("app.db.database.SessionLocal", lambda: FakeDB(connected)), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms):
        orch._stage_tumblr("task-100i", "Owl Coloring Page", OUTPUT, "4535264041", report)
    return report["stages"].get("tumblr", {}), captured


# ── [1] connected + listing asset -> posts the mockup + listing URL ──────────
print("[1] connected + a watermarked listing mockup -> posts to Tumblr with mockup + listing URL...")

with tempfile.TemporaryDirectory() as tmp:
    mockup = Path(tmp) / "hero.png"; PILImage.new("RGB", (1024, 1024), (200, 200, 210)).save(mockup)
    delivery = Path(tmp) / "design.png"; PILImage.new("RGB", (1024, 1024), (10, 10, 10)).save(delivery)
    # catalog returns the listing mockup (use_case listing); the delivery is NOT a listing asset
    stage, cap = _run_tumblr_stage(tmp, connected=True, listing_assets=[_asset(mockup, "listing")])

listing = cap.get("listing", {})
chan_ok = cap.get("channel") is not None and type(cap.get("channel")).__name__ == "TumblrChannel"
img_ok = listing.get("image_path") == str(mockup) and listing.get("image_path") != str(delivery)
url_ok = "4535264041" in (listing.get("listing_url") or "")
if stage.get("ok") and stage.get("success") and chan_ok and img_ok and url_ok:
    ok("[1] posted to Tumblr with the watermarked mockup (not the raw delivery) and the listing URL")
else:
    fail("[1] tumblr post", f"stage={stage}, chan_ok={chan_ok}, img_ok={img_ok}, url_ok={url_ok}, listing={listing}")


# ── [2] not connected -> skipped ─────────────────────────────────────────────
print("[2] Tumblr not connected -> skipped, no post attempted...")

with tempfile.TemporaryDirectory() as tmp:
    mockup = Path(tmp) / "hero.png"; PILImage.new("RGB", (1024, 1024), (200, 200, 210)).save(mockup)
    stage, cap = _run_tumblr_stage(tmp, connected=False, listing_assets=[_asset(mockup, "listing")])

if stage.get("skipped") and not cap:
    ok("[2] not connected -> cleanly skipped, no Tumblr post attempted")
else:
    fail("[2] skip when disconnected", f"stage={stage}, cap={cap}")


# ── [3] no listing asset -> skipped ──────────────────────────────────────────
print("[3] no listing image asset -> skipped...")

with tempfile.TemporaryDirectory() as tmp:
    stage, cap = _run_tumblr_stage(tmp, connected=True, listing_assets=[])

if stage.get("skipped") and not cap:
    ok("[3] no listing asset -> cleanly skipped")
else:
    fail("[3] skip when no asset", f"stage={stage}, cap={cap}")


# ── [4] idempotent -> skipped if already posted ──────────────────────────────
print("[4] already posted to Tumblr -> skipped (idempotent)...")

with tempfile.TemporaryDirectory() as tmp:
    mockup = Path(tmp) / "hero.png"; PILImage.new("RGB", (1024, 1024), (200, 200, 210)).save(mockup)
    already = SimpleNamespace(channel="tumblr", status="success")
    stage, cap = _run_tumblr_stage(tmp, connected=True, listing_assets=[_asset(mockup, "listing")], existing_posts=[already])

if stage.get("skipped") and not cap:
    ok("[4] idempotent -> skipped when a successful Tumblr post already exists")
else:
    fail("[4] idempotency", f"stage={stage}, cap={cap}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")
sys.exit(0 if _failed == 0 else 1)
