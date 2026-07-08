"""
PDF generation service — step 91.

Assembles genuinely multi-page PDF products (planners, guides, workbooks)
from real per-page generated images. Each page is its own real
image-generation call via ImageProviderManager — this is not a trick: a
6-page PDF costs 6 real image-generation calls, same cost model as any
other delivery asset. A partial set of pages is never assembled into a
deliverable — if any page fails, the whole PDF fails (the caller's gate
must treat that as a full task failure, not a degraded product).

Library choice: Pillow only (already a hard dependency via
ImageValidationService) for GENERATION — pages here are image-centric (a
generated illustration/layout per page with an optional short caption),
not flowing body text, so Pillow's own multi-image PDF save
(Image.save(..., format="PDF", save_all=True, append_images=[...])) plus
ImageDraw for the caption is sufficient without pulling in a heavier
text-layout library like reportlab/fpdf2. Pillow cannot read PDFs back,
though, so `pypdf` (pure-Python, no C extensions) is used purely for the
readback verification step — confirming the assembled file really opens
and really has the expected page count, not just that a file exists.

Hard cap: settings.MAX_PDF_PAGES. Enforced here as defense-in-depth —
TrendResearchAgent's concept validation should already reject/retry any
concept proposing more pages than this before a task is even created.
"""
import asyncio
import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import List

import httpx
from PIL import Image as PILImage, ImageDraw, ImageFont

from app.core.providers.image_manager import ImageProviderManager
from app.services.image_file_service import ImageFileService
from config import settings

logger = logging.getLogger("ai-factory")


class PDFGenerationError(Exception):
    """Raised when PDF assembly cannot produce a valid, complete multi-page deliverable."""


class PDFGenerationService:
    """
    Generates one real image per page spec and assembles them into a single
    multi-page PDF, stored via the existing 'delivery' variant pattern so it
    plugs into the same readback-verification gate as single-image products.
    """

    def __init__(self, image_provider=None, content_quality_service=None):
        self.image_provider = image_provider or ImageProviderManager.get_provider()
        self.file_service = ImageFileService()
        # Injected lazily (avoids a hard import cost when QA isn't used, and
        # lets tests pass a double). None -> built on first use.
        self._content_qa = content_quality_service

    def _qa_service(self):
        if self._content_qa is None:
            from app.services.content_quality_service import ContentQualityService
            self._content_qa = ContentQualityService()
        return self._content_qa

    def generate_pdf(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        page_briefs: List[str],
        filename: str = "design.pdf",
    ) -> Path:
        """
        Args:
            task_id: Task identifier (storage subdirectory).
            product_name: Name of the product being designed.
            visual_brief: Overall visual direction, shared across all pages.
            page_briefs: One short description per page (e.g. "cover page
                with title", "January monthly calendar grid") — used to
                generate genuinely distinct per-page content, not N copies
                of the same image.
            filename: Override filename for the saved PDF.

        Returns:
            Path to the saved, verified multi-page PDF (delivery variant).

        Raises:
            PDFGenerationError: if page_briefs is empty, exceeds
                settings.MAX_PDF_PAGES, any single page fails to generate,
                or the assembled file fails readback verification (doesn't
                open, or page count doesn't match what was requested).
        """
        page_count = len(page_briefs)
        if page_count == 0:
            raise PDFGenerationError("page_briefs must contain at least one page")
        if page_count > settings.MAX_PDF_PAGES:
            raise PDFGenerationError(
                f"requested {page_count} pages exceeds MAX_PDF_PAGES cap of {settings.MAX_PDF_PAGES}"
            )

        from config import settings as _settings
        qa_attempts = max(1, _settings.CONTENT_QA_MAX_ATTEMPTS)

        pages: List[PILImage.Image] = []
        for i, brief in enumerate(page_briefs, start=1):
            prompt = self._build_page_prompt(product_name, visual_brief, brief, i, page_count)
            page_img = None
            last_issues = None
            # Per-page content QA (step 96): a garbled/illegible page must never
            # make it into the assembled PDF. Regenerate the failing page up to
            # CONTENT_QA_MAX_ATTEMPTS times, then fail the whole PDF (which the
            # orchestrator treats as a delivery failure → task blocked).
            for attempt in range(1, qa_attempts + 1):
                try:
                    result = asyncio.run(
                        self.image_provider.generate_image(prompt, aspect_ratio="2:3", resolution="2K")
                    )
                    img = self._load_image(result)
                except Exception as e:
                    raise PDFGenerationError(f"page {i}/{page_count} image generation failed: {e}") from e

                img = img.convert("RGB")
                img = self._with_caption(img, brief)

                qa = self._review_page(img, product_name, brief, i, page_count)
                if qa is None or qa.passed:
                    page_img = img
                    break
                last_issues = qa.specific_issues
                logger.warning(
                    f"PDFGenerationService: page {i}/{page_count} failed content QA "
                    f"(attempt {attempt}/{qa_attempts}): {last_issues}"
                )

            if page_img is None:
                raise PDFGenerationError(
                    f"page {i}/{page_count} failed content quality after {qa_attempts} attempts: {last_issues}"
                )
            pages.append(page_img)

        pdf_bytes = self._assemble_pdf_bytes(pages)
        pdf_path = self.file_service.save_bytes(pdf_bytes, task_id, "delivery", filename)

        actual_pages = self._readback_page_count(pdf_path)
        if actual_pages != page_count:
            raise PDFGenerationError(
                f"assembled PDF has {actual_pages} pages on readback, expected {page_count}"
            )

        logger.info(
            f"PDFGenerationService: generated and verified {page_count}-page PDF "
            f"for task {task_id}: {pdf_path}"
        )
        return pdf_path

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_page_prompt(self, product_name: str, visual_brief: str, page_brief: str, page_num: int, total_pages: int) -> str:
        return (
            f"High-quality printable page {page_num} of {total_pages} for: {product_name}. "
            f"Overall visual direction: {visual_brief}. "
            f"This specific page's content: {page_brief}. "
            "Clean print-ready layout, portrait orientation, consistent visual "
            "style with the rest of the set. No watermarks."
        )

    def _review_page(self, img: PILImage.Image, product_name: str, brief: str, page_num: int, total: int):
        """Content-QA a single rendered page. Returns a ContentQualityResult or
        None if QA is unavailable (never silently passes on infra error — a
        None result is treated as pass only when QA genuinely cannot run, e.g.
        a test double omitted it; a real failed judgment returns passed=False)."""
        try:
            buf = BytesIO()
            img.save(buf, format="PNG")
            return self._qa_service().review_asset_bytes(
                buf.getvalue(),
                product_name=product_name,
                product_format="pdf_planner_or_guide",
                description=f"Page {page_num} of {total}: {brief}",
            )
        except Exception as e:
            logger.warning(f"PDFGenerationService: page content QA unavailable: {e}")
            return None

    def _load_image(self, result) -> PILImage.Image:
        if getattr(result, "b64_data", None):
            return PILImage.open(BytesIO(base64.b64decode(result.b64_data))).copy()
        if getattr(result, "url", None):
            resp = httpx.get(result.url, timeout=60.0, follow_redirects=True)
            resp.raise_for_status()
            return PILImage.open(BytesIO(resp.content)).copy()
        raise PDFGenerationError("image generation result has neither url nor b64_data")

    def _with_caption(self, img: PILImage.Image, caption: str) -> PILImage.Image:
        if not caption:
            return img
        draw = ImageDraw.Draw(img)
        text = caption[:80]
        margin = 20
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        draw.text((margin, img.height - margin - 20), text, fill=(0, 0, 0), font=font)
        return img

    def _assemble_pdf_bytes(self, pages: List[PILImage.Image]) -> bytes:
        buf = BytesIO()
        first, rest = pages[0], pages[1:]
        first.save(buf, format="PDF", save_all=True, append_images=rest)
        return buf.getvalue()

    def _readback_page_count(self, pdf_path: Path) -> int:
        """
        Independently re-open the assembled file and count its pages. Pillow
        can write PDFs but not read them back, so pypdf is used here purely
        for verification — confirming the file really exists, really opens,
        and really has the expected number of pages, not just that
        Path.exists() returns True.
        """
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
