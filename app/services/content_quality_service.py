"""
Content-quality service — step 96.

The FIRST check in this pipeline that inspects actual CONTENT rather than
structure. Every prior gate (ImageValidationService, taxonomy/file/publish
readbacks) verifies only structural properties — resolution, aspect ratio,
file existence, matching IDs. None of them look at whether the pixels
actually form legible, coherent, correct, sellable content.

That gap let a real, permanent, unsellable deliverable through: a "Family
Recipe Greeting Card" whose recipe text read "...1 tbsp butter, 2 þutter,
2 pie crusts-" — a duplicated ingredient with a corrupted glyph, invented
and garbled by the image-GENERATION model (Seedream cannot reliably render
text). No structural check can catch this; it requires actually reading the
image.

This service sends the generated asset to a VISION-capable model (a
different capability from image generation — it CONSUMES an image and
returns judgment) and asks, strictly:
  - text_legible          : is any text sharp and readable, not garbled?
  - text_coherent         : is the text real, correct, non-duplicated,
                            sensible English (not hallucinated gibberish)?
  - matches_intended_content : does this look like a genuine, complete,
                            finished, sellable version of the stated product
                            — not "is it pretty", but "is this actually the
                            real thing, or broken/incomplete/wrong"?
  - specific_issues       : concrete quotable problems.
passed is False if ANY boolean check fails.

Also provides check_marketing_consistency(): does a set of listing/marketing
photos plausibly depict the SAME product as the delivery asset — closing the
separate "buyer sees one thing, receives another" gap.
"""
import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.core.providers.manager import ProviderManager
from app.core.utils.json_sanitizer import JSONSanitizer
from config import settings

logger = logging.getLogger("ai-factory")


@dataclass
class ContentQualityResult:
    passed: bool
    text_legible: bool
    text_coherent: bool
    matches_intended_content: bool
    specific_issues: List[str] = field(default_factory=list)
    # Per-image mismatch breakdown for the marketing/deliverable consistency
    # check ONLY: one entry per marketing image that differs from the delivery
    # asset — {"image_index": <1-based index among the marketing photos>,
    # "issue": <why it differs>}. Empty for single-asset reviews and for a
    # consistent set. Lets the pipeline remake exactly the wrong image(s) with
    # targeted feedback instead of blocking the whole task.
    mismatches: List[dict] = field(default_factory=list)
    raw: Optional[str] = None
    error: Optional[str] = None


def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


# Delivery assets are generated at 2K/4K. A vision model bills per image
# token (roughly per 512px tile), so sending a 4K image is several times more
# expensive per QA call than needed — 1024px is ample to read text and judge
# legibility/coherence. Downscale before sending to keep this mandatory
# per-product stage's cost predictable.
_QA_MAX_DIM = 1024


def _downscale_for_review(image_bytes: bytes) -> bytes:
    try:
        from io import BytesIO
        from PIL import Image as PILImage
        img = PILImage.open(BytesIO(image_bytes))
        img = img.convert("RGB")
        img.thumbnail((_QA_MAX_DIM, _QA_MAX_DIM))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Not a decodable image (e.g. a test stub) — send as-is.
        return image_bytes


def _image_to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(_downscale_for_review(image_bytes)).decode('ascii')}"


def _delivery_image_bytes(path: Path) -> bytes:
    """Return image bytes a vision model can actually ingest for a delivery asset.

    Single-image deliverables are read directly. A PDF deliverable cannot be
    sent to a vision model as-is — the provider rejects any non-image payload
    with `invalid_image_format` (jpeg/webp/gif/png only), which previously
    blocked *every* pdf_planner_or_guide product at the marketing/deliverable
    consistency gate. Our PDFs are assembled by Pillow from exactly one
    full-page image per page (see PDFGenerationService), so the first page's
    single embedded image IS the cover — extract it with pypdf (already a hard
    dependency; no PDF rasterizer / system library needed) and re-encode it as
    PNG. This gives the consistency check a faithful, decodable representation
    of what the buyer actually receives.
    """
    if path.suffix.lower() == ".pdf":
        from io import BytesIO
        from PIL import Image as PILImage
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        images = reader.pages[0].images if reader.pages else []
        if not images:
            raise ValueError(f"PDF delivery asset has no extractable page image: {path}")
        buf = BytesIO()
        images[0].image.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    return path.read_bytes()


class ContentQualityService:
    def __init__(self, provider=None, model: str = None):
        self.provider = provider or ProviderManager.get_provider()
        self.model = model or settings.CONTENT_QA_MODEL
        self.sanitizer = JSONSanitizer()

    @staticmethod
    def _charge_vision():
        """P0-13: record each vision-QA call in the daily ledger (cheap, but keeps
        the spend accounting honest). Best-effort; never breaks the review."""
        try:
            from app.services.autonomy_service import AutonomyService
            cost = getattr(settings, "VISION_QA_COST_USD", 0.002)
            AutonomyService().record_spend(cost, "vision QA")
            # #4: per-task cost ledger (attributed via cost_context).
            from app.core.cost_context import record_cost
            record_cost(cost, use_case="vision_qa", provider="openrouter",
                        model=getattr(settings, "CONTENT_QA_MODEL", ""))
        except Exception:
            pass

    # ── Single-asset content review ───────────────────────────────────────────

    def review_asset_bytes(
        self,
        image_bytes: bytes,
        product_name: str,
        product_format: str,
        description: str = "",
    ) -> ContentQualityResult:
        prompt = self._build_review_prompt(product_name, product_format, description)
        data_url = _image_to_data_url(image_bytes)
        try:
            raw = _run_async(
                self.provider.generate_with_images(
                    model=self.model,
                    prompt=prompt,
                    image_data_urls=[data_url],
                    temperature=0.0,
                )
            )
        except Exception as e:
            logger.error(f"ContentQualityService: vision call failed: {e}")
            # Infra failure is NOT a pass. The caller treats a non-passed
            # result as a gate failure (retry, then block) rather than
            # letting unreviewed content through.
            return ContentQualityResult(
                passed=False, text_legible=False, text_coherent=False,
                matches_intended_content=False,
                specific_issues=[f"content-quality vision call failed: {e}"],
                error=str(e),
            )
        self._charge_vision()
        return self._parse_review(raw)

    def review_asset_file(
        self,
        image_path,
        product_name: str,
        product_format: str,
        description: str = "",
    ) -> ContentQualityResult:
        p = Path(image_path)
        if not p.exists():
            return ContentQualityResult(
                passed=False, text_legible=False, text_coherent=False,
                matches_intended_content=False,
                specific_issues=[f"asset file not found: {p}"],
            )
        return self.review_asset_bytes(p.read_bytes(), product_name, product_format, description)

    # ── Strict per-page review for PDF planners/guides ────────────────────────

    def review_pdf_page_bytes(
        self,
        image_bytes: bytes,
        product_name: str,
        page_desc: str = "",
    ) -> ContentQualityResult:
        """Stricter review for one page of a printable planner/guide PDF: it must
        be a clean, functional, print-ready LAYOUT with real, correctly-spelled
        text — NOT a photograph/decorative render, and no garbled text or stray
        meta-text. Uses settings.PDF_QA_MODEL (defaults to the single-image QA
        model)."""
        prompt = self._build_pdf_page_review_prompt(product_name, page_desc)
        data_url = _image_to_data_url(image_bytes)
        model = getattr(settings, "PDF_QA_MODEL", None) or self.model
        try:
            raw = _run_async(
                self.provider.generate_with_images(
                    model=model,
                    prompt=prompt,
                    image_data_urls=[data_url],
                    temperature=0.0,
                )
            )
        except Exception as e:
            logger.error(f"ContentQualityService: pdf-page vision call failed: {e}")
            return ContentQualityResult(
                passed=False, text_legible=False, text_coherent=False,
                matches_intended_content=False,
                specific_issues=[f"pdf-page content-quality vision call failed: {e}"],
                error=str(e),
            )
        self._charge_vision()
        return self._parse_review(raw)

    def _build_pdf_page_review_prompt(self, product_name: str, page_desc: str) -> str:
        return f"""
You are a STRICT quality-control reviewer for a printable PLANNER/GUIDE PDF a
paying customer downloads and PRINTS. You are shown ONE page of it.

  Product: {product_name}
  What this page is supposed to be: {page_desc}

A page a customer will happily print and use is a CLEAN, FUNCTIONAL LAYOUT:
headings, tables/grids, lists, ruled lines, checkboxes, labelled boxes, generous
white space, high-contrast text on a white background.

Judge harshly — the customer paid real money for a usable, professional page.

Return ONLY valid JSON, no markdown:
{{
  "text_legible": true/false,          // every word is sharp and readable
  "text_coherent": true/false,         // ALL text is real, correctly-spelled, sensible English — set FALSE if ANY word is garbled, misspelled, duplicated, cut off, or nonsensical (e.g. "Print-Iready" instead of "Print-ready"), OR if stray meta-text like "page 4 of 6" / "print-ready" / a watermark is printed onto the page
  "matches_intended_content": true/false,  // set FALSE if the page is a PHOTOGRAPH or has decorative/clip-art imagery (food, objects, scenery, people) instead of a clean functional layout, if the content is unrelated to this page's stated purpose, or if the layout is broken, cluttered, or not something a person could actually fill in / use
  "specific_issues": ["concrete, quotable problems (e.g. 'photo of a pineapple on a meal-planner page', 'garbled word: Print-Iready')"]
}}
"""

    # ── Marketing/deliverable consistency ─────────────────────────────────────

    def check_marketing_consistency(
        self,
        delivery_path,
        marketing_paths: List,
        product_name: str,
    ) -> ContentQualityResult:
        """
        Does each marketing/listing photo plausibly depict the SAME product
        design as the delivery asset (allowing for presentation differences —
        a flat design vs the same design shown in a mockup/context), or do
        they show clearly different, unrelated content? Returns passed=False
        with issues if any marketing image is unrelated to what's delivered.
        """
        delivery = Path(delivery_path)
        marketing = [Path(m) for m in marketing_paths if Path(m).exists()]
        if not delivery.exists() or not marketing:
            # Nothing to compare — not a failure by itself.
            return ContentQualityResult(
                passed=True, text_legible=True, text_coherent=True,
                matches_intended_content=True,
                specific_issues=["no comparable images"],
            )
        data_urls = [_image_to_data_url(_delivery_image_bytes(delivery))]
        data_urls += [_image_to_data_url(m.read_bytes()) for m in marketing]
        prompt = self._build_consistency_prompt(product_name, len(marketing))
        try:
            raw = _run_async(
                self.provider.generate_with_images(
                    model=self.model,
                    prompt=prompt,
                    image_data_urls=data_urls,
                    temperature=0.0,
                )
            )
        except Exception as e:
            logger.error(f"ContentQualityService: consistency vision call failed: {e}")
            return ContentQualityResult(
                passed=False, text_legible=False, text_coherent=False,
                matches_intended_content=False,
                specific_issues=[f"consistency vision call failed: {e}"],
                error=str(e),
            )
        self._charge_vision()
        return self._parse_consistency(raw)

    # ── Prompts ───────────────────────────────────────────────────────────────

    def _build_review_prompt(self, product_name: str, product_format: str, description: str) -> str:
        return f"""
You are a strict quality-control reviewer for a print-on-demand / digital
product shop. You are shown the ACTUAL deliverable file a paying customer
would receive for this product:

  Product name: {product_name}
  Product format: {product_format}
  Intended description: {description}

Judge ONLY whether this is a real, finished, sellable deliverable — not
whether it is pretty or on-brand. Be harsh: a customer paid real money for
this exact file.

Check especially for image-generation text artifacts: garbled/misspelled
words, corrupted or non-Latin glyphs substituted into English words (e.g.
"þ" inside a word), duplicated or nonsensical lines, cut-off text, or
placeholder/lorem-ipsum-like gibberish.

Return ONLY valid JSON, no markdown:
{{
  "text_legible": true/false,          // any text present is sharp and readable (true if there is no text at all)
  "text_coherent": true/false,         // any text is real, correctly spelled, non-duplicated, sensible (true if no text)
  "matches_intended_content": true/false,  // this is a genuine, complete, finished version of the stated product
  "specific_issues": ["concrete, quotable problems"]  // empty if none
}}
"""

    def _build_consistency_prompt(self, product_name: str, n_marketing: int) -> str:
        return f"""
You are checking a shop listing for buyer-misrepresentation. You are shown
{n_marketing + 1} images in total for "{product_name}":

- The FIRST image (image 0) is the ACTUAL delivery file the buyer receives.
  Treat it as the ground-truth design.
- The remaining {n_marketing} image(s) are the marketing/preview photos on the
  listing, numbered 1 to {n_marketing} in the order shown (marketing image 1 is
  the 2nd image overall, marketing image 2 is the 3rd, and so on).

IMPORTANT: these images come from SEPARATE, independent image generations. They
are NOT expected to be pixel-identical to the delivery file. Judge ONLY whether
each marketing image depicts the SAME core subject, design, and content as the
delivered product — not whether it looks stylistically identical.

REPORT A MISMATCH only if the marketing image shows a genuinely DIFFERENT
design/theme/subject than the delivered product — something a buyer would
reasonably feel MISLED by. For example:
  - different text/wording than the delivered design,
  - a different illustrated scene, character, object, or motif,
  - a different pattern, or a clearly different core design,
  - a generic stock mockup unrelated to the actual delivered file,
  - the wrong product entirely.

DO NOT report a mismatch for INCIDENTAL variation that any two independent
generations of the same underlying design naturally produce, including:
  - a different background color, texture, or backdrop,
  - a different font style/rendering of the SAME words,
  - different lighting, color grading, or saturation,
  - decorative embellishment style, framing, cropping, or overall artistic
    treatment,
  - showing the design flat vs. in a room / on a surface / held in a hand.
These are acceptable and MUST NOT be flagged.

When you DO flag a mismatch, the "issue" field MUST describe what is different
about the CORE SUBJECT / DESIGN / CONTENT specifically (e.g. "shows a different
illustrated animal", "the title text reads differently"). Do NOT flag something
whose only difference is the background, font, color, lighting, or styling — if
that is all that differs, it is a MATCH, not a mismatch.

Return ONLY valid JSON, no markdown, with EXACTLY this shape:
{{
  "consistent": true/false,   // true unless a marketing image shows a genuinely different core subject/design
  "mismatches": [             // one entry per mismatched marketing image; [] if all match
    {{"image_index": 1, "issue": "how THIS image's CORE subject/design/content differs from the delivered design"}}
  ]
}}
"""

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse_review(self, raw: str) -> ContentQualityResult:
        try:
            data = json.loads(raw)
        except Exception:
            try:
                data = self.sanitizer.extract(raw)
            except Exception as e:
                logger.warning(f"ContentQualityService: could not parse review JSON: {e}")
                return ContentQualityResult(
                    passed=False, text_legible=False, text_coherent=False,
                    matches_intended_content=False,
                    specific_issues=[f"unparseable content-quality response: {raw[:200]}"],
                    raw=raw, error=str(e),
                )

        legible = bool(data.get("text_legible", False))
        coherent = bool(data.get("text_coherent", False))
        matches = bool(data.get("matches_intended_content", False))
        issues = data.get("specific_issues") or []
        if not isinstance(issues, list):
            issues = [str(issues)]
        passed = legible and coherent and matches
        return ContentQualityResult(
            passed=passed,
            text_legible=legible,
            text_coherent=coherent,
            matches_intended_content=matches,
            specific_issues=[str(i) for i in issues],
            raw=raw,
        )

    def _parse_consistency(self, raw: str) -> ContentQualityResult:
        """Parse the marketing/deliverable consistency verdict.

        Accepts the current per-image schema —
          {"consistent": bool, "mismatches": [{"image_index": int, "issue": str}]}
        — and stays backward-compatible with the older overall schema
          {"matches_intended_content": bool, "specific_issues": [...]}
        (still emitted by existing test doubles), so a schema swap can't
        silently turn every consistency check into a pass/fail wrong answer.
        """
        try:
            data = json.loads(raw)
        except Exception:
            try:
                data = self.sanitizer.extract(raw)
            except Exception as e:
                logger.warning(f"ContentQualityService: could not parse consistency JSON: {e}")
                return ContentQualityResult(
                    passed=False, text_legible=False, text_coherent=False,
                    matches_intended_content=False,
                    specific_issues=[f"unparseable consistency response: {raw[:200]}"],
                    raw=raw, error=str(e),
                )

        mismatches = []
        for m in (data.get("mismatches") or []):
            if not isinstance(m, dict):
                continue
            try:
                idx = int(m.get("image_index"))
            except (TypeError, ValueError):
                continue
            issue = m.get("issue") or m.get("reason") or "does not match the delivered design"
            mismatches.append({"image_index": idx, "issue": str(issue)})

        # Verdict: explicit "consistent", else old "matches_intended_content",
        # else infer from whether any mismatch was reported.
        if "consistent" in data:
            consistent = bool(data.get("consistent"))
        elif "matches_intended_content" in data:
            consistent = bool(data.get("matches_intended_content"))
        else:
            consistent = len(mismatches) == 0

        issues = data.get("specific_issues")
        if not isinstance(issues, list) or not issues:
            issues = [f"marketing image {m['image_index']}: {m['issue']}" for m in mismatches]
        if not issues and not consistent:
            issues = ["marketing images do not match the delivered design"]

        return ContentQualityResult(
            passed=consistent,
            text_legible=True,
            text_coherent=True,
            matches_intended_content=consistent,
            specific_issues=[str(i) for i in issues],
            mismatches=mismatches,
            raw=raw,
        )
