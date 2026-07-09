"""
Step 96 test — content-quality vision gate (the check that should have existed)

Context: a real "Family Recipe Greeting Card" deliverable (design.png) had
recipe text reading "...1 tbsp butter, 2 þutter, 2 pie crusts-" — a
duplicated ingredient with a corrupted glyph, invented and garbled by the
image-GENERATION model (Seedream cannot reliably render text). It passed
every structural gate (image validation, taxonomy/file/publish readbacks)
because none of them inspect actual content. This gate does.

Tests (use a FAKE vision provider — no real API calls):
  [1] ContentQualityService rejects a garbled/duplicated-text asset
      (text_coherent=false), passed=False, issues surfaced.
  [2] ContentQualityService passes a clean asset.
  [3] Orchestrator: content gate rejects a bad delivery asset, regenerates
      up to CONTENT_QA_MAX_ATTEMPTS, then blocks the task — and NOTHING is
      uploaded to Etsy (create_draft_listing never called) since the gate
      runs before listing creation.
  [4] Orchestrator: clean asset passes the gate and the pipeline proceeds to
      create a listing.
  [5] check_marketing_consistency rejects marketing photos depicting
      unrelated content vs the delivery asset.
  [6] (step 100e recalibration) the consistency PROMPT gives tiered guidance:
      accept incidental style variance (background/font/lighting/color), reject
      only a genuinely different core subject/design.
  [7] task 73fb29ba scenario (same design, only background+font differ) -> PASS.
  [8] GUARANTEE: a genuinely different core subject/design still FAILS (the
      original bug class this gate exists for must never regress).
  [9] borderline grayscale-vs-colored (same subject) -> resolved ACCEPT.

Usage:
  python scripts/test_step96_content_quality.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test96.db", delete=False)
_tmp.close()
os.environ.pop("DATABASE_PATH", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))

import logging
logging.basicConfig(level=logging.WARNING)

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

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 96 — content-quality vision gate tests\n")


# ── Fake vision provider ─────────────────────────────────────────────────────

class FakeVisionProvider:
    """Returns a canned JSON verdict for generate_with_images. `verdicts` is a
    list consumed in order (so regeneration can 'improve' on retry)."""
    def __init__(self, verdicts):
        self._verdicts = list(verdicts)
        self.calls = 0

    async def generate_with_images(self, model, prompt, image_data_urls, **kwargs):
        self.calls += 1
        v = self._verdicts.pop(0) if self._verdicts else self._verdicts_default()
        return json.dumps(v)


GARBLED = {
    "text_legible": False,
    "text_coherent": False,
    "matches_intended_content": False,
    "specific_issues": ["ingredient 'butter' appears twice; corrupted glyph 'þ' inside a word ('2 þutter')"],
}
CLEAN = {
    "text_legible": True,
    "text_coherent": True,
    "matches_intended_content": True,
    "specific_issues": [],
}
UNRELATED_MARKETING = {
    "text_legible": True,
    "text_coherent": True,
    "matches_intended_content": False,
    "specific_issues": ["marketing photo shows a floral wreath card unrelated to the delivered recipe design"],
}

# ── Step 100e recalibration: incidental style variance PASSES; only a genuinely
#    different core subject/design FAILS. Verdicts below represent what a
#    correctly-calibrated vision model returns for each scenario (the prompt
#    that instructs it is proven separately in test [6]). ────────────────────
INCIDENTAL_STYLE_OK = {   # task 73fb29ba: same text/design, different background + font
    "consistent": True,
    "mismatches": [],
}
DIFFERENT_SUBJECT = {     # never-regress guarantee: a genuinely different design/subject
    "consistent": False,
    "mismatches": [
        {"image_index": 1, "issue": "shows a completely different mandala pattern / illustrated subject than the delivered design"}
    ],
}
GRAYSCALE_VS_COLOR_OK = {  # borderline resolved as ACCEPT (color/saturation is incidental)
    "consistent": True,
    "mismatches": [],
}


# ── [1] service rejects garbled content ──────────────────────────────────────
print("[1] ContentQualityService rejects garbled/duplicated text...")

svc1 = ContentQualityService(provider=FakeVisionProvider([GARBLED]), model="fake")
r1 = svc1.review_asset_bytes(b"fakepng", "Family Recipe Greeting Card", "greeting_card_design", "recipe card")
if r1.passed is False and not r1.text_coherent and r1.specific_issues:
    ok("[1] garbled asset rejected (passed=False), issues surfaced")
else:
    fail("[1] garbled rejection", f"result={r1}")


# ── [2] service passes clean content ─────────────────────────────────────────
print("[2] ContentQualityService passes a clean asset...")

svc2 = ContentQualityService(provider=FakeVisionProvider([CLEAN]), model="fake")
r2 = svc2.review_asset_bytes(b"fakepng", "Botanical Line Art", "single_print", "line art print")
if r2.passed is True and r2.text_legible and r2.matches_intended_content:
    ok("[2] clean asset passes")
else:
    fail("[2] clean pass", f"result={r2}")


# ── Orchestrator helpers ─────────────────────────────────────────────────────

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


def _fake_image_path(tmp_dir, name="asset.png"):
    p = Path(tmp_dir) / name
    PILImage.new("RGB", (1024, 1024), color=(90, 140, 200)).save(p, format="PNG")
    return p


class OkPODPipelineService:
    def __init__(self, design_path):
        self._design_path = design_path
    def build_product_record(self, task_id, product_name, visual_brief, product_type):
        return {"task_id": task_id, "design_path": str(self._design_path), "ready_for_pod": True}


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


def _patch_common(tmp):
    hero = _fake_image_path(tmp, "hero.png")
    pia = MagicMock(); pia.return_value.generate_listing_images.return_value = {"hero": hero, "lifestyle": None}
    lga = MagicMock(); lga.return_value.generate_listing.return_value = {"title": "t"}
    pis = MagicMock(); pis.return_value.enrich_listing_with_image.return_value = {"image_base64": "x", "pin_image_path": str(hero)}
    ms = MagicMock(); ms.return_value.get_posts_for_task.return_value = []; ms.return_value.post_to_channel.return_value = {"success": True}
    return pia, lga, pis, ms


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


def _run_orch(tmp, cq_verdicts, consistency_verdict=CLEAN):
    """Run the orchestrator for a single_print task with a fake vision provider
    driving the content gate. Returns (report, etsy)."""
    design = _fake_image_path(tmp, "design.png")
    task = _make_done_task("Botanical Line Art Print", "single_print")
    orch = PipelineOrchestrator()
    pia, lga, pis, ms = _patch_common(tmp)
    etsy = FakeEtsyClientHappy()

    # The content gate builds ContentQualityService() with no args -> real
    # provider. Patch the class to inject our fake vision provider, sharing one
    # provider across content-review and consistency calls (verdicts in order:
    # all cq_verdicts first, then the consistency verdict).
    provider = FakeVisionProvider(list(cq_verdicts) + [consistency_verdict])

    def _cq_factory(*a, **k):
        return ContentQualityService(provider=provider, model="fake")

    with patch("app.services.pipeline_orchestrator.ProductImageAgent", pia), \
         patch("app.services.pipeline_orchestrator.PODPipelineService", lambda: OkPODPipelineService(design)), \
         patch("app.services.pipeline_orchestrator.ListingGeneratorAgent", lga), \
         patch("app.services.pipeline_orchestrator.EtsyClient", return_value=etsy), \
         patch("app.services.pipeline_orchestrator.EtsyImageService", _fake_eis()), \
         patch("app.services.pipeline_orchestrator.PinterestImageService", pis), \
         patch("app.services.pipeline_orchestrator.MarketingService", ms), \
         patch("app.services.content_quality_service.ContentQualityService", _cq_factory):
        report = orch.run_post_completion(task.id)
    return report, etsy


# ── [3] orchestrator blocks garbled content before any Etsy upload ───────────
print("[3] orchestrator: garbled delivery asset -> regenerate, then block, NO Etsy upload...")

with tempfile.TemporaryDirectory() as tmp:
    # Garbled on every attempt (CONTENT_QA_MAX_ATTEMPTS=2) -> block.
    report3, etsy3 = _run_orch(tmp, [GARBLED, GARBLED])

cq3 = report3["stages"].get("content_quality", {})
if (
    report3.get("blocked") is True
    and cq3.get("ok") is False
    and "content quality" in (report3.get("blocked_reason") or "")
    and not etsy3.created  # create_draft_listing NEVER called
):
    ok("[3] garbled asset blocked before listing creation; zero Etsy uploads")
else:
    fail("[3] content gate block", f"report={report3}, etsy_created={etsy3.created}")


# ── [4] clean asset passes, pipeline proceeds ────────────────────────────────
print("[4] orchestrator: clean delivery asset passes the gate, listing created...")

with tempfile.TemporaryDirectory() as tmp:
    report4, etsy4 = _run_orch(tmp, [CLEAN])

cq4 = report4["stages"].get("content_quality", {})
if cq4.get("ok") is True and etsy4.created and not report4.get("blocked"):
    ok("[4] clean asset passes content gate; listing created")
else:
    fail("[4] content gate pass", f"report={report4}, etsy_created={etsy4.created}")


# ── [5] marketing/deliverable consistency rejects unrelated photos ───────────
print("[5] check_marketing_consistency rejects unrelated marketing photos...")

with tempfile.TemporaryDirectory() as tmp:
    delivery = _fake_image_path(tmp, "delivery.png")
    marketing = _fake_image_path(tmp, "marketing.png")
    svc5 = ContentQualityService(provider=FakeVisionProvider([UNRELATED_MARKETING]), model="fake")
    r5 = svc5.check_marketing_consistency(delivery, [marketing], "Family Recipe Greeting Card")

if r5.passed is False and r5.specific_issues:
    ok("[5] unrelated marketing photos rejected")
else:
    fail("[5] consistency rejection", f"result={r5}")


# ── [6] consistency prompt gives tiered guidance (the real recalibration) ─────
print("[6] consistency prompt: accept incidental style variance, reject different core subject...")

svc6 = ContentQualityService(provider=FakeVisionProvider([]), model="fake")
p6 = svc6._build_consistency_prompt("Family Recipe Greeting Card", 2).lower()
# explicitly instructs the model these are independent (not pixel-identical) gens
independent = "independent" in p6 and ("not" in p6 and "pixel-identical" in p6)
# ACCEPT tier names the incidental dimensions that must NOT be flagged
accept_tier = all(tok in p6 for tok in ["background", "font", "lighting", "color grading"])
must_not_flag = ("do not report a mismatch" in p6) or ("must not be flagged" in p6)
# REJECT tier is anchored to core subject/design + buyer being misled
reject_tier = ("core subject" in p6) and ("different" in p6) and ("misled" in p6 or "mislead" in p6)
# the issue field is required to describe the CORE subject difference
issue_anchored = "core subject" in p6

if independent and accept_tier and must_not_flag and reject_tier and issue_anchored:
    ok("[6] prompt accepts background/font/lighting/color variance; reserves mismatch for a different core subject")
else:
    fail("[6] tiered prompt", f"independent={independent}, accept={accept_tier}, must_not={must_not_flag}, "
                              f"reject={reject_tier}, issue_anchored={issue_anchored}")


# ── [7] task 73fb29ba: same design, different background+font -> now PASSES ────
print("[7] task 73fb29ba scenario (same text/design, only background+font differ) -> PASS...")

with tempfile.TemporaryDirectory() as tmp:
    delivery = _fake_image_path(tmp, "delivery.png")
    marketing = _fake_image_path(tmp, "marketing.png")
    svc7 = ContentQualityService(provider=FakeVisionProvider([INCIDENTAL_STYLE_OK]), model="fake")
    r7 = svc7.check_marketing_consistency(delivery, [marketing], "Motivational Quote Print")

if r7.passed is True and not r7.mismatches:
    ok("[7] incidental background/font-only variation accepted (no mismatch, no needless remake)")
else:
    fail("[7] incidental accept", f"result={r7}")


# ── [8] GUARANTEE: a genuinely different design/subject still FAILS ───────────
print("[8] GUARANTEE: a genuinely different core subject/design is still detected and FAILS...")

with tempfile.TemporaryDirectory() as tmp:
    delivery = _fake_image_path(tmp, "delivery.png")
    marketing = _fake_image_path(tmp, "marketing.png")
    svc8 = ContentQualityService(provider=FakeVisionProvider([DIFFERENT_SUBJECT]), model="fake")
    r8 = svc8.check_marketing_consistency(delivery, [marketing], "Mandala Wall Art")

core_issue = bool(r8.mismatches) and any(
    kw in (r8.mismatches[0].get("issue", "").lower()) for kw in ("mandala", "subject", "different")
)
if r8.passed is False and core_issue:
    ok("[8] genuinely different core subject/design still fails (original bug class stays caught)")
else:
    fail("[8] different-subject guarantee", f"result={r8}")


# ── [9] borderline: grayscale delivery vs colored marketing -> resolved ACCEPT ─
# Decision: a color-palette/saturation-only difference of the SAME subject is
# INCIDENTAL (color grading/saturation is in the ACCEPT tier), and the delivery
# asset is already the primary featured photo, so the buyer sees the true version
# first. Resolved as ACCEPT (same reasoning as coloring_page: colored marketing
# vs line-art delivery of the same design).
print("[9] borderline: same subject, grayscale delivery vs vividly colored marketing -> ACCEPT...")

with tempfile.TemporaryDirectory() as tmp:
    delivery = _fake_image_path(tmp, "delivery.png")
    marketing = _fake_image_path(tmp, "marketing.png")
    svc9 = ContentQualityService(provider=FakeVisionProvider([GRAYSCALE_VS_COLOR_OK]), model="fake")
    r9 = svc9.check_marketing_consistency(delivery, [marketing], "Botanical Line Art")

if r9.passed is True:
    ok("[9] color-palette-only difference of the SAME subject accepted (documented decision)")
else:
    fail("[9] borderline accept", f"result={r9}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

import os as _os
try:
    _os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
