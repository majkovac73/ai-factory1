"""
Step 100d test — coloring_page marketing images must be HONEST line-art, not
photographic/colored renders that inherently conflict with the consistency gate.

Context: a coloring_page's deliverable is intentionally BLANK line-art, but
ProductImageAgent's generic hero/lifestyle prompts requested "professional
product photography" / "lifestyle photography", which produce a colored,
finished-looking render. That can NEVER match the delivered blank line-art, so
the marketing/deliverable consistency gate rejected it forever (real prod task
8d9f8e58 — the remake fired but couldn't converge). Fix: make the hero/lifestyle
prompts format-aware so coloring_page depicts the actual uncolored line-art
product (hero) and realistic context around the still-uncolored page (lifestyle).

The consistency check itself is intentionally UNCHANGED — the fix is in what's
generated, not in loosening what's verified.

Tests (fake image provider captures the exact generation prompts — no real API):
  [1] generate_listing_images(product_format="coloring_page") -> hero AND
      lifestyle prompts explicitly use line-art / uncolored / blank framing and
      do NOT use the generic "professional product photography" / "lifestyle
      photography" language.
  [2] generate_listing_images(product_format="single_print") -> still the generic
      photography prompts, and NONE of the coloring-page line-art framing —
      proving the special-casing is per-format, not global.
  [3] product_format=None -> generic prompts (regression / default unchanged).
  [4] regenerate_listing_image(role=hero/lifestyle, product_format="coloring_page")
      -> the REMAKE path is also format-aware (line-art framing + the corrective
      guidance both present in the prompt).
  [5] regenerate_listing_image(role=hero, product_format="single_print") -> generic
      (distinct from coloring_page), proving the remake path branches by format too.

Usage:
  python scripts/test_step100d_coloring_page_prompts.py
"""
import base64
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.NamedTemporaryFile(suffix=".test100d.db", delete=False)
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

_passed = _failed = 0


def ok(label):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label, reason):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


print("\nStep 100d — coloring_page honest line-art marketing prompts\n")


def _png_b64():
    buf = BytesIO()
    PILImage.new("RGB", (1024, 1024), (240, 240, 240)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class CapturingProvider:
    """Records every generation prompt; returns a valid PNG so saving succeeds."""
    def __init__(self):
        self.prompts = []

    async def generate_image(self, prompt, aspect_ratio="1:1", resolution="2K"):
        self.prompts.append(prompt)
        return SimpleNamespace(url=None, b64_data=_png_b64())


PRODUCT = "Personalized Family Portrait Coloring Page"
BRIEF = "a symmetrical family-portrait outline template for kids to color"

# Framing that MUST appear for coloring_page (honest, uncolored line-art).
LINEART_TOKENS = ["line art", "line-art", "uncolored", "blank"]
# Generic photography language that must NOT appear for coloring_page.
GENERIC_TOKENS = ["professional product photography", "lifestyle photography style", "aspirational"]


def _has_any(text, tokens):
    t = text.lower()
    return any(tok.lower() in t for tok in tokens)


def _agent():
    return ProductImageAgent(image_provider=CapturingProvider())


# ── [1] coloring_page generation prompts are honest line-art framing ─────────
print('[1] generate_listing_images(coloring_page) -> hero+lifestyle use line-art / uncolored framing...')

a1 = _agent()
a1.generate_listing_images(task_id="t100d-1", product_name=PRODUCT, visual_brief=BRIEF, product_format="coloring_page")
hero_p, life_p = a1.image_provider.prompts[0], a1.image_provider.prompts[1]

hero_ok = _has_any(hero_p, LINEART_TOKENS) and not _has_any(hero_p, GENERIC_TOKENS) and "coloring page" in hero_p.lower()
life_ok = _has_any(life_p, LINEART_TOKENS) and not _has_any(life_p, GENERIC_TOKENS)
if hero_ok and life_ok:
    ok("[1] coloring_page hero+lifestyle prompts are honest line-art/uncolored, not generic photography")
else:
    fail("[1] coloring framing", f"hero_ok={hero_ok}, life_ok={life_ok}\n  hero={hero_p!r}\n  life={life_p!r}")


# ── [2] other formats keep the generic prompts and NONE of the line-art framing ─
print('[2] generate_listing_images(single_print) -> generic photography, no line-art framing (distinct)...')

a2 = _agent()
a2.generate_listing_images(task_id="t100d-2", product_name="Botanical Line Print", visual_brief="minimal botanical print", product_format="single_print")
hero2, life2 = a2.image_provider.prompts[0], a2.image_provider.prompts[1]

# "line" appears in "Botanical Line Print"; check specifically for the coloring-page
# uncolored/blank framing tokens, not the substring "line".
distinct_tokens = ["uncolored", "blank line", "coloring page", "still uncolored"]
generic_used = _has_any(hero2, ["professional product photography"]) and _has_any(life2, ["lifestyle photography style"])
no_coloring_framing = not _has_any(hero2, distinct_tokens) and not _has_any(life2, distinct_tokens)
if generic_used and no_coloring_framing:
    ok("[2] single_print keeps generic photography prompts; no coloring-page framing leaked in")
else:
    fail("[2] format distinct", f"generic_used={generic_used}, no_coloring_framing={no_coloring_framing}\n  hero={hero2!r}\n  life={life2!r}")


# ── [3] product_format=None -> generic (default unchanged) ───────────────────
print('[3] generate_listing_images(product_format=None) -> generic prompts (regression)...')

a3 = _agent()
a3.generate_listing_images(task_id="t100d-3", product_name="Sticker Sheet", visual_brief="cute stickers")
hero3 = a3.image_provider.prompts[0]
if _has_any(hero3, ["professional product photography"]) and not _has_any(hero3, ["uncolored", "blank line"]):
    ok("[3] default (no format) still uses the generic hero prompt")
else:
    fail("[3] default generic", f"hero={hero3!r}")


# ── [4] the REMAKE path is also format-aware for coloring_page ───────────────
print('[4] regenerate_listing_image(coloring_page) -> line-art framing + corrective guidance in prompt...')

# NOTE: keep this marker free of coloring-page tokens (uncolored/blank/line-art)
# so test [5] can assert the *base* single_print prompt carries none of them.
GUIDANCE = "CORRECTIVE-MARKER: must match the delivered design exactly, not a different or fictional render"
a4 = _agent()
a4.regenerate_listing_image(
    task_id="t100d-4", product_name=PRODUCT, visual_brief=BRIEF,
    role="hero", corrective_guidance=GUIDANCE, filename="hero.png", product_format="coloring_page",
)
a4.regenerate_listing_image(
    task_id="t100d-4", product_name=PRODUCT, visual_brief=BRIEF,
    role="lifestyle", corrective_guidance=GUIDANCE, filename="lifestyle.png", product_format="coloring_page",
)
rhero, rlife = a4.image_provider.prompts[0], a4.image_provider.prompts[1]
remake_ok = (
    _has_any(rhero, LINEART_TOKENS) and "CORRECTIVE-MARKER" in rhero and not _has_any(rhero, GENERIC_TOKENS)
    and _has_any(rlife, LINEART_TOKENS) and "CORRECTIVE-MARKER" in rlife
)
if remake_ok:
    ok("[4] remake path uses coloring_page line-art framing AND carries the corrective guidance")
else:
    fail("[4] remake framing", f"\n  hero={rhero!r}\n  life={rlife!r}")


# ── [5] remake path for a different format stays generic (distinct) ──────────
print('[5] regenerate_listing_image(single_print) -> generic remake prompt (distinct from coloring_page)...')

a5 = _agent()
a5.regenerate_listing_image(
    task_id="t100d-5", product_name="Botanical Print", visual_brief="minimal botanical",
    role="hero", corrective_guidance=GUIDANCE, filename="hero.png", product_format="single_print",
)
r5 = a5.image_provider.prompts[0]
if _has_any(r5, ["professional product photography"]) and "CORRECTIVE-MARKER" in r5 and not _has_any(r5, ["uncolored", "blank line"]):
    ok("[5] single_print remake stays generic (no coloring-page framing), still carries guidance")
else:
    fail("[5] remake distinct", f"prompt={r5!r}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\nResults: {_passed} passed, {_failed} failed\n")

try:
    os.unlink(_tmp.name)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
