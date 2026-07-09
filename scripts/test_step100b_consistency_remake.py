"""
Step 100b test — marketing/deliverable consistency: targeted, feedback-driven
remake of the mismatched image(s) instead of an immediate hard block.

Context: real prod task 194a1933 was BLOCKED_NO_PRODUCT because the consistency
check found marketing images depicting a different design (floral border,
alternate mandala) than the actual delivery asset — even though the delivery
asset itself passed content-quality QA. Blocking the whole task for an
independently-generated marketing photo is too blunt. Now: use the vision
model's OWN per-image mismatch description as corrective feedback, regenerate
ONLY the mismatched marketing image(s), re-check, and repeat up to a hard cap
of settings.MARKETING_CONSISTENCY_MAX_REMAKES (2) before falling back to the
original block behavior.

Tests (FAKE vision provider + FAKE image agent — no real API calls, no cost):
  [1]  Fail-then-pass: attempt 1 reports a specific mismatched image_index +
       issue; the orchestrator regenerates JUST that image (not the delivery
       asset, not the other correct marketing image), feeds the vision model's
       issue text + delivery ground-truth into the remake as corrective
       guidance, re-checks, PASSES, and the task proceeds to create a listing.
  [1b] The real ProductImageAgent.regenerate_listing_image actually embeds the
       corrective guidance into the image-generation prompt (proves the
       feedback reaches generation, not just the agent boundary).
  [2]  Persistent mismatch through BOTH allowed remakes -> falls back to
       BLOCKED_NO_PRODUCT, no listing created, and EXACTLY 2 remake attempts
       were made (cap enforced, no unbounded regeneration).
  [3]  Safety net (cap = 0, remakes disabled): a consistency failure still
       blocks correctly with zero remakes — the original step-96 hard-block
       behavior is preserved.

Usage:
  python scripts/test_step100b_consistency_remake.py
"""
import base64
import json
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test100b.db", delete=False)
_tmp.close()
os.environ.pop("DATABASE_PATH", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))

import logging
logging.basicConfig(level=logging.ERROR)

from dotenv import load_dotenv
load_dotenv()

from app.db.database import Base, engine
import app.models.task, app.models.log, app.models.analytics_event
import app.models.image_asset, app.models.marketing_post
Base.metadata.create_all(bind=engine)

from PIL import Image as PILImage

from app.services.task_service import TaskService
from app.schemas.task import TaskCreate
from app.schemas.enums import TaskStatus
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.services.content_quality_service import ContentQualityService
from app.agents.image.product_image_agent import ProductImageAgent

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 100b — consistency remake-with-feedback tests\n")


# ── Vision verdicts ──────────────────────────────────────────────────────────
CLEAN_REVIEW = {  # single-asset content review verdict (delivery + regenerated marketing image)
    "text_legible": True, "text_coherent": True,
    "matches_intended_content": True, "specific_issues": [],
}
CONSISTENT = {"consistent": True, "mismatches": []}
# New per-image schema: marketing image 2 (hero) is wrong; delivery is image 0,
# marketing image 1 is the prepended delivery photo (must NOT be regenerated).
MISMATCH_HERO = {
    "consistent": False,
    "mismatches": [
        {"image_index": 2, "issue": "the second marketing image shows a floral border, not the delivered mandala design"}
    ],
}


class OrderedVisionProvider:
    """Pops canned verdicts in order and counts calls, so a wrong call ordering
    surfaces as a call-count/verdict mismatch rather than silently passing."""
    def __init__(self, verdicts):
        self._verdicts = list(verdicts)
        self.calls = 0

    async def generate_with_images(self, model, prompt, image_data_urls, **kwargs):
        self.calls += 1
        if not self._verdicts:
            # Ran off the end -> deliberately return an obviously-wrong verdict
            # so an ordering bug can't masquerade as success.
            return json.dumps({"consistent": False, "mismatches": [{"image_index": 99, "issue": "unexpected extra vision call"}]})
        return json.dumps(self._verdicts.pop(0))


# ── Fakes shared with the step-96 orchestrator harness ───────────────────────

def _make_done_task(prompt, task_type):
    ts = TaskService()
    t = ts.create_task(TaskCreate(prompt=prompt, type=task_type))
    ts.update_status(t.id, TaskStatus.PLANNED.value)
    ts.update_status(t.id, TaskStatus.RUNNING.value)
    ts.update_status(t.id, TaskStatus.QA.value)
    ts.save_qa_result(t.id, output_data={
        "title": prompt, "description": f"A specific product: {prompt}",
        "keywords": ["test"], "sections": [],
    }, error_message=None)
    ts.update_status(t.id, TaskStatus.DONE.value)
    return ts.get_task(t.id)


def _real_png(path, size=(1024, 1024), color=(90, 140, 200)):
    PILImage.new("RGB", size, color).save(path, format="PNG")
    return Path(path)


class RecordingImageAgent:
    """Fake ProductImageAgent. Generates real hero+lifestyle PNGs, and records
    every targeted regeneration (which file, what corrective guidance) so the
    test can prove ONLY the mismatched image was remade and with what feedback.
    """
    workdir = None
    regen_calls = []

    def __init__(self, *a, **k):
        pass

    def generate_listing_images(self, task_id, product_name, visual_brief, **k):
        hero = _real_png(Path(RecordingImageAgent.workdir) / "hero.png", color=(200, 90, 90))
        life = _real_png(Path(RecordingImageAgent.workdir) / "lifestyle.png", color=(90, 200, 90))
        return {"hero": hero, "lifestyle": life}

    def regenerate_listing_image(self, task_id, product_name, visual_brief, slot, corrective_guidance, filename, **k):
        RecordingImageAgent.regen_calls.append({
            "slot": slot, "filename": filename, "guidance": corrective_guidance,
        })
        # Mimic the real method's in-place overwrite: same FILENAME, fresh bytes
        # (a fresh subdir stands in for "new content at the stable path"), so a
        # subsequent remake still targets "hero.png", not a drifted name.
        sub = Path(RecordingImageAgent.workdir) / f"regen{len(RecordingImageAgent.regen_calls)}"
        sub.mkdir(parents=True, exist_ok=True)
        return _real_png(sub / filename, color=(120, 120, 220))


class FakeEtsyClientHappy:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._sent = {}
    async def create_draft_listing(self, listing):
        lid = f"L-{len(self.created)+1}"
        self.created.append(lid)
        self._sent[lid] = listing
        return {"listing_id": lid}
    async def get_listing(self, listing_id):
        sent = self._sent.get(listing_id, {})
        return {"listing_id": listing_id, "taxonomy_id": sent.get("taxonomy_id"), "when_made": sent.get("when_made")}
    async def delete_listing(self, listing_id):
        self.deleted.append(listing_id)
        return True


def _fake_eis():
    m = MagicMock()
    st = {"n": 0}
    async def _attach(listing_id, listing_image_paths, digital_file_path=None):
        st["n"] = len(listing_image_paths)
        return {"listing_id": listing_id,
                "uploaded_images": [{"path": p, "result": {"ok": True}} for p in listing_image_paths],
                "digital_upload": {"ok": True} if digital_file_path else None,
                "publish_result": {"published": True, "state": "active"}}
    async def _gi(listing_id): return [{"listing_image_id": i} for i in range(st["n"])]
    async def _gf(listing_id): return [{"listing_file_id": 1, "filetype": "image/png"}]
    m.return_value.attach_images_and_publish.side_effect = _attach
    m.return_value.get_listing_images.side_effect = _gi
    m.return_value.get_listing_files.side_effect = _gf
    return m


class OkPODPipelineService:
    def __init__(self, design_path):
        self._design_path = design_path
    def build_product_record(self, task_id, product_name, visual_brief, product_type):
        return {"task_id": task_id, "design_path": str(self._design_path), "ready_for_pod": True}


def _run(tmp, vision_verdicts):
    """Run the full post-completion pipeline for a single_print task with a
    shared ordered vision provider and the recording image agent."""
    RecordingImageAgent.workdir = tmp
    RecordingImageAgent.regen_calls = []

    design = _real_png(Path(tmp) / "design.png", color=(60, 60, 160))
    task = _make_done_task("Mandala Wall Art Print", "single_print")

    provider = OrderedVisionProvider(vision_verdicts)
    def _cq_factory(*a, **k):
        return ContentQualityService(provider=provider, model="fake")

    lga = MagicMock(); lga.return_value.generate_listing.return_value = {"title": "t"}
    pis = MagicMock(); pis.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(design)}
    ms = MagicMock(); ms.return_value.get_posts_for_task.return_value = []; ms.return_value.post_to_channel.return_value = {"success": True}
    etsy = FakeEtsyClientHappy()

    orch = PipelineOrchestrator()
    with patch("app.services.pipeline_orchestrator.ProductImageAgent", RecordingImageAgent), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", _fake_eis()), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms), \
         patch("app.services.content_quality_service.ContentQualityService", _cq_factory):
        report = orch.run_post_completion(task.id)
    return report, etsy, provider


# ── [1] fail then pass: only the mismatched image is remade, with feedback ────
print("[1] mismatch on attempt 1 -> remake JUST that image with feedback -> passes...")

with tempfile.TemporaryDirectory() as tmp:
    # delivery review CLEAN, consistency FAIL(hero), regen-hero review CLEAN, recheck CONSISTENT
    report1, etsy1, prov1 = _run(tmp, [CLEAN_REVIEW, MISMATCH_HERO, CLEAN_REVIEW, CONSISTENT])

calls = RecordingImageAgent.regen_calls
mc = report1["stages"].get("marketing_consistency", {})
only_hero = len(calls) == 1 and calls[0]["filename"] == "hero.png" and calls[0]["slot"] == "hero"
no_delivery_or_lifestyle = all(c["filename"] not in ("design.png", "lifestyle.png") for c in calls)
guidance = calls[0]["guidance"] if calls else ""
feedback_included = ("floral border" in guidance) and ("Mandala" in guidance)
proceeded = bool(etsy1.created) and not report1.get("blocked") and mc.get("ok") is True

if only_hero and no_delivery_or_lifestyle and feedback_included and proceeded and mc.get("remakes") == 1:
    ok("[1] only the mismatched hero remade (not delivery/lifestyle); feedback in prompt; task proceeded")
else:
    fail("[1] targeted remake", f"only_hero={only_hero}, no_del_life={no_delivery_or_lifestyle}, "
                                f"feedback={feedback_included}, proceeded={proceeded}, mc={mc}, calls={calls}")


# ── [1b] the real agent embeds corrective guidance into the generation prompt ─
print("[1b] ProductImageAgent.regenerate_listing_image embeds the corrective guidance in the prompt...")

class CapturingImageProvider:
    def __init__(self): self.prompts = []
    async def generate_image(self, prompt, aspect_ratio="1:1", resolution="2K"):
        self.prompts.append(prompt)
        buf = BytesIO(); PILImage.new("RGB", (1024, 1024), (10, 20, 30)).save(buf, format="PNG")
        return SimpleNamespace(url=None, b64_data=base64.b64encode(buf.getvalue()).decode("ascii"))

cap = CapturingImageProvider()
agent = ProductImageAgent(image_provider=cap)
GUIDANCE = "GUIDANCE-MARKER: must depict the delivered mandala design, not a floral border"
out_path = agent.regenerate_listing_image(
    task_id="unit-1b", product_name="Mandala Wall Art Print",
    visual_brief="intricate symmetrical mandala line art", slot="hero",
    corrective_guidance=GUIDANCE, filename="hero.png",
)
prompt_used = cap.prompts[-1] if cap.prompts else ""
if "GUIDANCE-MARKER" in prompt_used and "mandala" in prompt_used.lower() and Path(out_path).exists():
    ok("[1b] corrective guidance is present in the actual generation prompt")
else:
    fail("[1b] guidance in prompt", f"prompt={prompt_used[:200]!r}")


# ── [2] persistent mismatch -> block after EXACTLY 2 remakes ──────────────────
print("[2] persistent mismatch -> BLOCKED_NO_PRODUCT after exactly 2 remakes, no listing...")

with tempfile.TemporaryDirectory() as tmp:
    # delivery CLEAN, then (FAIL, regen CLEAN)x2 rechecks all FAIL -> block
    report2, etsy2, prov2 = _run(
        tmp, [CLEAN_REVIEW, MISMATCH_HERO, CLEAN_REVIEW, MISMATCH_HERO, CLEAN_REVIEW, MISMATCH_HERO]
    )

calls2 = RecordingImageAgent.regen_calls
mc2 = report2["stages"].get("marketing_consistency", {})
exactly_two = len(calls2) == 2 and all(c["filename"] == "hero.png" for c in calls2)
blocked = report2.get("blocked") is True and mc2.get("ok") is False
no_listing = not etsy2.created

if exactly_two and blocked and no_listing and mc2.get("remakes") == 2 and "mismatch" in (report2.get("blocked_reason") or ""):
    ok("[2] exactly 2 remakes then hard block; zero Etsy listings created")
else:
    fail("[2] cap + fallback block", f"exactly_two={exactly_two}, blocked={blocked}, no_listing={no_listing}, "
                                     f"mc={mc2}, calls={len(calls2)}")


# ── [3] safety net: remakes disabled (cap=0) still blocks, zero remakes ───────
print("[3] with MARKETING_CONSISTENCY_MAX_REMAKES=0, a mismatch blocks immediately (no remakes)...")

with tempfile.TemporaryDirectory() as tmp:
    from config import settings as _settings
    with patch.object(_settings, "MARKETING_CONSISTENCY_MAX_REMAKES", 0):
        # delivery CLEAN, consistency FAIL -> immediate block, no remake calls
        report3, etsy3, prov3 = _run(tmp, [CLEAN_REVIEW, MISMATCH_HERO])

calls3 = RecordingImageAgent.regen_calls
mc3 = report3["stages"].get("marketing_consistency", {})
if not calls3 and report3.get("blocked") is True and mc3.get("ok") is False and not etsy3.created and mc3.get("remakes") == 0:
    ok("[3] cap=0 preserves original hard-block behavior with zero remakes")
else:
    fail("[3] safety net", f"calls={len(calls3)}, blocked={report3.get('blocked')}, mc={mc3}, created={etsy3.created}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

try:
    os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
