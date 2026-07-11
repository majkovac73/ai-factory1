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
(Image.save(..., format="PDF", save_all=True, append_images=[...])) is
sufficient without pulling in a heavier text-layout library like
reportlab/fpdf2. Pillow cannot read PDFs back,
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
from PIL import Image as PILImage

from app.core.providers.image_manager import ImageProviderManager
from app.services.image_file_service import ImageFileService
from config import settings

logger = logging.getLogger("ai-factory")

# Per-page image request parameters. Pages are portrait, so aspect_ratio="2:3".
# Seedream 4.5 rejects any request below 3,686,400 pixels with an HTTP 400
# ("image size must be at least 3686400 pixels"). A 2:3 page at "2K" is only
# ~2.8M pixels (1365×2048) — below that floor — which is exactly what failed in
# production (task 127d5130). "4K" at 2:3 produces 2732×4096 = ~11.2M pixels,
# comfortably above the floor. Seedream is flat-rate ($0.04/image) regardless of
# resolution, so this is a pure correctness fix with no cost change. This mirrors
# the same 2K→4K fix already applied to the Pinterest pin (see
# social_image_agent.PINTEREST_RESOLUTION).
PDF_PAGE_ASPECT_RATIO = "2:3"
PDF_PAGE_RESOLUTION = "4K"


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
        render_interior: bool = False,
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
            # A-6: code-render interior pages (i>1) deterministically with
            # Pillow — always-legible grids/lines/checkboxes at ~$0, no garbled
            # text, no vision QA needed. Page 1 stays an image-generated cover.
            if render_interior and i > 1:
                try:
                    from app.services.planner_page_renderer import PlannerPageRenderer
                    spec = PlannerPageRenderer.derive_spec(brief)
                    pages.append(PlannerPageRenderer().render(spec).convert("RGB"))
                    logger.info(f"PDFGenerationService: page {i}/{page_count} rendered ({spec['layout']})")
                    continue
                except Exception as e:
                    logger.warning(
                        f"PDFGenerationService: planner render failed for page {i} "
                        f"({e}); falling back to image generation"
                    )

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
                        self.image_provider.generate_image(
                            prompt,
                            aspect_ratio=PDF_PAGE_ASPECT_RATIO,
                            resolution=PDF_PAGE_RESOLUTION,
                        )
                    )
                    img = self._load_image(result)
                except Exception as e:
                    raise PDFGenerationError(f"page {i}/{page_count} image generation failed: {e}") from e

                img = img.convert("RGB")
                # P1-3: no caption stamp — the old _with_caption drew the raw
                # page brief in a ~10px default font on the final 4K image,
                # producing a microscopic, unprofessional caption on the paid
                # deliverable that also violated the page prompt's "no meta-text"
                # rule. The generated layout already contains its own headings.

                qa = self._review_page(img, product_name, brief, i, page_count)
                if qa is not None and qa.passed:
                    page_img = img
                    break
                # P1-4: fail CLOSED. A None result means the vision QA could NOT
                # run (rate limit / key issue) — previously that silently PASSED
                # the page, shipping an unreviewed (possibly garbled) page to a
                # paying customer. Now it's a failed attempt like any other:
                # retry, then block the whole PDF (consistent with the single-
                # image gate, which already fails closed).
                if qa is None:
                    last_issues = ["page QA unavailable (vision review could not run)"]
                else:
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
        # Steer HARD toward a clean functional layout: this is the single biggest
        # lever against the two real failure modes seen in production — Seedream
        # dropping a decorative PHOTO (e.g. a pineapple) onto a planner page, and
        # rendering GARBLED meta-text ("Print-Iready", "page 4 of 6"). Ask for
        # only the usable layout, minimal correctly-spelled labels, no imagery,
        # no meta-text.
        return (
            f"A clean, minimal, PRINT-READY planner/guide page: {page_brief}. "
            f"Part of the product '{product_name}'. Visual style: {visual_brief}. "
            "Design it as a FUNCTIONAL layout a person prints and writes on: clear "
            "section headings, tables/grids, lists, ruled lines, checkboxes and empty "
            "boxes, with generous white space, on a solid WHITE background with "
            "high-contrast black line-work, portrait orientation, consistent with the "
            "rest of the set. "
            "Use ONLY short, real, correctly-spelled English headings/labels that fit "
            "this page's purpose. "
            "Do NOT include any photographs, decorative illustrations, clip-art, or "
            "food/object/scenery/people imagery; do NOT print paragraphs of body text, "
            "page numbers, 'page X of Y', 'print-ready', the shop or product name, or "
            "any watermark. Just the clean, usable page layout."
        )

    def _review_page(self, img: PILImage.Image, product_name: str, brief: str, page_num: int, total: int):
        """Content-QA a single rendered page. Returns a ContentQualityResult, or
        None if the vision QA could not run at all (infra error). Per P1-4 the
        caller treats None as a FAILED attempt (fail closed), never a pass."""
        try:
            buf = BytesIO()
            img.save(buf, format="PNG")
            svc = self._qa_service()
            # Prefer the STRICT per-page reviewer (rejects photos / garbled text on
            # a functional page); fall back to the generic asset review for older
            # doubles that don't implement it.
            strict = getattr(svc, "review_pdf_page_bytes", None)
            if callable(strict):
                return strict(
                    buf.getvalue(),
                    product_name=product_name,
                    page_desc=f"Page {page_num} of {total}: {brief}",
                )
            return svc.review_asset_bytes(
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

    # Etsy rejects digital files over 20MB; assemble well under that.
    _ETSY_MAX_PDF_BYTES = 19_000_000
    _PDF_LONG_EDGE_PX = 2200  # ~260dpi on letter — still print-quality

    def _assemble_pdf_bytes(self, pages: List[PILImage.Image]) -> bytes:
        """P1-5: keep the assembled PDF under Etsy's 20MB per-file cap. Six 4K
        (11MP) pages easily exceed it, which would 4xx the upload AFTER paying
        for all page generations. Downscale each page to a print-quality long
        edge, and if still over, re-encode pages as JPEG inside the PDF."""
        scaled = [self._scale_for_pdf(p) for p in pages]

        pdf_bytes = self._save_pages_pdf(scaled)
        if len(pdf_bytes) <= self._ETSY_MAX_PDF_BYTES:
            return pdf_bytes

        # Still too big — re-encode with in-PDF JPEG compression (quality 85).
        logger.warning(
            f"PDFGenerationService: assembled PDF is {len(pdf_bytes)} bytes (> "
            f"{self._ETSY_MAX_PDF_BYTES}); re-encoding pages as JPEG q85"
        )
        pdf_bytes = self._save_pages_pdf(
            [p.convert("RGB") for p in scaled],
            extra={"quality": 85, "optimize": True},
        )
        if len(pdf_bytes) > self._ETSY_MAX_PDF_BYTES:
            raise PDFGenerationError(
                f"assembled PDF is {len(pdf_bytes)} bytes, still over Etsy's "
                f"{self._ETSY_MAX_PDF_BYTES}-byte limit after downscale + JPEG re-encode"
            )
        return pdf_bytes

    def _scale_for_pdf(self, img: PILImage.Image) -> PILImage.Image:
        long_edge = max(img.size)
        if long_edge <= self._PDF_LONG_EDGE_PX:
            return img
        ratio = self._PDF_LONG_EDGE_PX / float(long_edge)
        new_size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        return img.resize(new_size, PILImage.LANCZOS)

    def _save_pages_pdf(self, pages: List[PILImage.Image], extra: dict = None) -> bytes:
        buf = BytesIO()
        first, rest = pages[0], pages[1:]
        kwargs = {"format": "PDF", "save_all": True, "append_images": rest, "resolution": 150.0}
        if extra:
            kwargs.update(extra)
        first.save(buf, **kwargs)
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
