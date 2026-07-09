"""
Step 100f test — pdf_planner_or_guide marketing images must depict REAL page
content, not a generic decorative book cover.

Context: real prod task b68b099a (5-page PDF) was blocked because its marketing
images showed "a book cover with decorative elements" bearing no relation to the
actual page content inside the delivered PDF. Same class as the coloring_page
issue (step 100d): a generic prompt template produces imagery that can never
structurally match this format's real deliverable. Fix: extend the existing
per-format prompt branching (ProductImageAgent._format_override) with a
pdf_planner_or_guide branch whose hero = flatlay of the ACTUAL interior pages and
lifestyle = someone using a real page — grounded in the real generated page
topics (content_context), not an invented cover.

Tests (fake image provider captures the exact prompts — no real API/generation):
  [1] generate_listing_images(product_format="pdf_planner_or_guide",
      content_context=<real page topics>) -> hero AND lifestyle prompts reference
      real INTERIOR pages / the actual page topics and explicitly reject "book
      cover" framing.
  [2] The pdf prompts are DISTINCT from both the generic default and the
      coloring_page template (no line-art/uncolored language, no generic
      "professional product photography" as the framing).
  [3] regenerate_listing_image(...pdf_planner_or_guide...) -> the remake path is
      also pdf-aware (interior-page framing + real page topics + the corrective
      guidance all present).
  [4] The orchestrator grounds the context in the real generated page topics:
      _marketing_content_context(is_pdf=True, {sections:[...]}) -> the topics; and
      non-pdf / no-sections -> "".

Usage:
  python scripts/test_step100f_pdf_planner_prompts.py
"""
import base64
import os
import sys
import tempfile
from io import BytesIO
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test100f.db", delete=False)
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
import app.models.task, app.models.log
Base.metadata.create_all(bind=engine)

from PIL import Image as PILImage

from app.agents.image.product_image_agent import ProductImageAgent
from app.services.pipeline_orchestrator import PipelineOrchestrator

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 100f — pdf_planner_or_guide honest real-page marketing prompts\n")


def _png_b64():
    buf = BytesIO()
    PILImage.new("RGB", (1024, 1024), (240, 240, 240)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class CapturingProvider:
    def __init__(self):
        self.prompts = []

    async def generate_image(self, prompt, aspect_ratio="1:1", resolution="2K"):
        self.prompts.append(prompt)
        return SimpleNamespace(url=None, b64_data=_png_b64())


def _agent():
    return ProductImageAgent(image_provider=CapturingProvider())


PRODUCT = "Weekly Meal Planner & Grocery Guide"
BRIEF = "a printable weekly meal-planning PDF"
PAGES = "Weekly Menu Grid; Grocery Shopping List; Meal Prep Tips; Pantry Inventory; Budget Tracker"

# Real-page framing that MUST appear; "book cover" framing that must be rejected.
REALPAGE_TOKENS = ["interior page", "interior pages", "real page", "actual"]
COVER_REJECT = "book cover"  # prompt must explicitly say NOT a book cover
COLORING_TOKENS = ["uncolored", "blank line", "line art"]
GENERIC_TOKENS = ["professional product photography", "lifestyle photography style"]


def _has_any(text, tokens):
    t = text.lower()
    return any(tok.lower() in t for tok in tokens)


# ── [1] pdf generation prompts reference real interior pages + reject cover ───
print('[1] generate_listing_images(pdf_planner_or_guide) -> hero+lifestyle show REAL interior pages, not a cover...')

a1 = _agent()
a1.generate_listing_images(task_id="t100f-1", product_name=PRODUCT, visual_brief=BRIEF,
                           product_format="pdf_planner_or_guide", content_context=PAGES)
hero_p, life_p = a1.image_provider.prompts[0], a1.image_provider.prompts[1]

hero_ok = (
    _has_any(hero_p, REALPAGE_TOKENS)
    and COVER_REJECT in hero_p.lower()          # explicitly names/negates "book cover"
    and "Grocery Shopping List" in hero_p        # grounded in the REAL page topics
)
life_ok = (
    _has_any(life_p, REALPAGE_TOKENS)
    and ("writing" in life_p.lower() or "filling in" in life_p.lower())
    and "Grocery Shopping List" in life_p
)
if hero_ok and life_ok:
    ok("[1] pdf hero+lifestyle reference the real interior pages/topics and reject a decorative cover")
else:
    fail("[1] pdf real-page framing", f"hero_ok={hero_ok}, life_ok={life_ok}\n  hero={hero_p!r}\n  life={life_p!r}")


# ── [2] distinct from the generic default AND the coloring_page template ──────
print('[2] pdf prompts are distinct from the generic default and the coloring_page template...')

a2 = _agent()
a2.generate_listing_images(task_id="t100f-2", product_name="Botanical Print", visual_brief="a print",
                           product_format="single_print")
gen_hero = a2.image_provider.prompts[0]

a3 = _agent()
a3.generate_listing_images(task_id="t100f-3", product_name="Animal Coloring Page", visual_brief="animals",
                           product_format="coloring_page")
col_hero = a3.image_provider.prompts[0]

# pdf prompt has neither generic photography framing nor coloring-page line-art framing
pdf_not_generic = not _has_any(hero_p, GENERIC_TOKENS)
pdf_not_coloring = not _has_any(hero_p, COLORING_TOKENS)
# and the others are genuinely different from the pdf prompt
generic_distinct = _has_any(gen_hero, ["professional product photography"]) and not _has_any(gen_hero, REALPAGE_TOKENS + [COVER_REJECT])
coloring_distinct = _has_any(col_hero, COLORING_TOKENS) and "book cover" not in col_hero.lower()

if pdf_not_generic and pdf_not_coloring and generic_distinct and coloring_distinct:
    ok("[2] pdf template is distinct from both the generic default and the coloring_page template")
else:
    fail("[2] distinctness", f"pdf_not_generic={pdf_not_generic}, pdf_not_coloring={pdf_not_coloring}, "
                             f"generic_distinct={generic_distinct}, coloring_distinct={coloring_distinct}")


# ── [3] the REMAKE path is also pdf-aware and carries the real page topics ────
print('[3] regenerate_listing_image(pdf_planner_or_guide) -> interior-page framing + topics + corrective guidance...')

GUIDANCE = "CORRECTIVE-MARKER: must show real interior planner pages, not a decorative cover"
a4 = _agent()
a4.regenerate_listing_image(
    task_id="t100f-4", product_name=PRODUCT, visual_brief=BRIEF, role="hero",
    corrective_guidance=GUIDANCE, filename="hero.png",
    product_format="pdf_planner_or_guide", content_context=PAGES,
)
r_hero = a4.image_provider.prompts[0]
remake_ok = (
    _has_any(r_hero, REALPAGE_TOKENS) and "book cover" in r_hero.lower()
    and "Grocery Shopping List" in r_hero and "CORRECTIVE-MARKER" in r_hero
)
if remake_ok:
    ok("[3] remake path uses pdf interior-page framing, real topics, and the corrective guidance")
else:
    fail("[3] remake framing", f"\n  hero={r_hero!r}")


# ── [4] orchestrator grounds content_context in the real generated page topics ─
print('[4] orchestrator _marketing_content_context: real sections for pdf, empty otherwise...')

orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
ctx_pdf = orch._marketing_content_context(True, {"sections": ["Weekly Menu Grid", "Grocery Shopping List", "Meal Prep Tips"]})
ctx_nonpdf = orch._marketing_content_context(False, {"sections": ["Weekly Menu Grid"]})
ctx_empty = orch._marketing_content_context(True, {})
if "Grocery Shopping List" in ctx_pdf and "Weekly Menu Grid" in ctx_pdf and ctx_nonpdf == "" and ctx_empty == "":
    ok("[4] content_context is the real page topics for pdf, and empty for non-pdf / no sections")
else:
    fail("[4] content_context", f"pdf={ctx_pdf!r}, nonpdf={ctx_nonpdf!r}, empty={ctx_empty!r}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

try:
    os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
