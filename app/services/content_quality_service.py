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


class ContentQualityService:
    def __init__(self, provider=None, model: str = None):
        self.provider = provider or ProviderManager.get_provider()
        self.model = model or settings.CONTENT_QA_MODEL
        self.sanitizer = JSONSanitizer()

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
        data_urls = [_image_to_data_url(delivery.read_bytes())]
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
        return self._parse_review(raw)

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
You are checking a shop listing for buyer-misrepresentation. The FIRST image
is the ACTUAL delivery file the buyer receives for "{product_name}". The
next {n_marketing} image(s) are the marketing/preview photos shown on the
listing.

Question: do the marketing photo(s) plausibly depict the SAME product/design
as the actual delivery file? Presentation may differ (a flat design vs the
same design shown framed or in a room) — that is fine. But if a marketing
photo shows a clearly DIFFERENT, unrelated design/content than what is
actually delivered (different artwork, different text, a generic stock
mockup unrelated to the real file), that is a misrepresentation and must
fail.

Return ONLY valid JSON, no markdown:
{{
  "text_legible": true,
  "text_coherent": true,
  "matches_intended_content": true/false,  // true only if marketing genuinely represents the delivered design
  "specific_issues": ["which marketing image differs and how"]
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
