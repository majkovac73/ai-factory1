"""
Product Image Generation Agent — step 69.

Generates the listing/preview images shown to buyers on the Etsy listing:
hero shot + lifestyle mockup. Uses the visual brief produced by
VisualDirectorAgent (step 52) as the core prompt source so the image
reflects the brand direction, not just the raw product name.

Agents request capability 'generate_image'; provider selection is handled
by ImageProviderManager — this agent never names a provider directly.
"""
import asyncio
from pathlib import Path
from typing import Optional

from app.agents.base_agent import BaseAgent
from app.core.providers.image_manager import ImageProviderManager
from app.services.image_file_service import ImageFileService
from config import settings


class ProductImageAgent(BaseAgent):
    """
    Generates listing/preview images for an Etsy product.

    Produces two images per product:
      - hero shot  (stored as 'listing' variant)
      - lifestyle mockup (stored as 'listing' variant, second file)

    Both are sized for Etsy's recommended listing image dimensions
    (1024x1024 square, suitable for Etsy's main listing thumbnail).
    """

    def __init__(self, provider=None, model: str = None, image_provider=None):
        super().__init__(provider, model)
        self.image_provider = image_provider or ImageProviderManager.get_provider()
        self.file_service = ImageFileService()

    def _format_override(self, product_format, role: str, product_name: str, visual_brief: str, content_context: str = ""):
        """Return a format-specific listing-image prompt for (product_format, role),
        or None to fall back to the generic photography prompt.

        This is where a format whose HONEST marketing imagery differs from generic
        product photography is special-cased, because a generic "professional
        product photography" / "lifestyle" prompt produces imagery that can NEVER
        structurally match that format's real deliverable — the marketing/
        deliverable consistency gate then (correctly) rejects it forever, no matter
        how many remakes run. Cases handled so far:
          - coloring_page: deliverable is intentionally blank line-art, not a
            colored render.
          - pdf_planner_or_guide: deliverable is real interior planner/guide PAGES,
            not a decorative book cover. `content_context` carries the actual
            generated page topics so the flatlay/in-use shots depict real content.

        Structured as a simple dispatch so adding another format later is a small,
        localized addition — not a rewrite of the generic path.
        """
        if product_format == "coloring_page":
            if role == "hero":
                return (
                    f"The ACTUAL blank line-art coloring page for '{product_name}', shown "
                    "clearly and flat on a plain white background exactly as it will look "
                    "when printed: crisp solid black outline line art on white paper, "
                    "completely UNCOLORED. "
                    f"Design of the line art: {visual_brief}. "
                    "It is a COLORING PAGE — do NOT color it in, do NOT show a finished, "
                    "painted, shaded or colored-in version; outlines only, white interior. "
                    "No watermarks."
                )
            if role == "lifestyle":
                return (
                    f"A realistic context photo of the printed blank line-art coloring page "
                    f"'{product_name}' lying on a table next to colored pencils and crayons, "
                    "with the page itself still clearly UNCOLORED crisp black-outline line "
                    "art (optionally a child's hand just beginning to color one small corner, "
                    "while the rest of the page remains blank line art). "
                    f"Design of the line art: {visual_brief}. "
                    "The coloring page must stay recognisable as the blank line-art product "
                    "the buyer actually receives — NOT a fully colored-in picture. "
                    "No watermarks."
                )

        if product_format == "pdf_planner_or_guide":
            # Ground the imagery in the REAL generated page content, not an invented
            # cover. `content_context` is the actual page topics from the PDF.
            pages_clause = (
                f" The real interior pages cover: {content_context}."
                if content_context else ""
            )
            if role == "hero":
                return (
                    f"An overhead flatlay of the ACTUAL printed interior pages of the "
                    f"multi-page '{product_name}' planner/guide PDF, arranged on a desk — "
                    "show one or two real INTERIOR pages with their actual layout visible "
                    "(headings, lists, tables, and writing/fill-in lines), exactly as the "
                    "pages look when printed. "
                    "This is NOT a closed book and NOT a decorative book cover — show the "
                    "real page content the buyer receives inside the PDF. "
                    f"Theme/brief: {visual_brief}.{pages_clause} "
                    "Bright, even lighting, clean desk, no watermarks."
                )
            if role == "lifestyle":
                return (
                    f"A realistic in-use photo of a person writing on / filling in one of "
                    f"the ACTUAL interior pages of the '{product_name}' planner/guide, pen "
                    "in hand, with the real page layout (headings, lists, writing lines) "
                    "clearly visible on the desk. "
                    "It must show a real INTERIOR page being used — NOT a closed book and "
                    "NOT a decorative cover. "
                    f"Theme/brief: {visual_brief}.{pages_clause} "
                    "Warm natural light, cozy desk setting, no watermarks."
                )
        return None

    def _build_hero_prompt(self, product_name: str, visual_brief: str, product_format=None, content_context: str = "") -> str:
        override = self._format_override(product_format, "hero", product_name, visual_brief, content_context)
        if override:
            return override
        return (
            f"Professional product photography style. Hero shot of: {product_name}. "
            f"Visual brief: {visual_brief}. "
            "Clean, high-quality image suitable for an online marketplace listing. "
            "No text, no watermarks, no borders."
        )

    def _build_lifestyle_prompt(self, product_name: str, visual_brief: str, product_format=None, content_context: str = "") -> str:
        override = self._format_override(product_format, "lifestyle", product_name, visual_brief, content_context)
        if override:
            return override
        return (
            f"Lifestyle photography style. Context/in-use shot of: {product_name}. "
            f"Visual brief: {visual_brief}. "
            "Warm, aspirational atmosphere. No text, no watermarks."
        )

    def generate_listing_images(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        size: str = None,
        aspect_ratio: str = "1:1",
        resolution: str = "2K",
        product_format=None,
        content_context: str = "",
    ) -> dict:
        """
        Generate hero + lifestyle images and save both as 'listing' variants.

        Args:
            task_id: Task identifier (used as storage subdirectory).
            product_name: Name/type of the product being listed.
            visual_brief: Output from VisualDirectorAgent (text description).
            size: Ignored; kept for API compatibility. OpenRouter uses aspect_ratio+resolution.
            aspect_ratio: OpenRouter aspect ratio string (default '1:1' for Etsy square).
            resolution: OpenRouter resolution tier (default '2K' — Seedream 4.5 minimum).
            product_format: Product format key (e.g. 'coloring_page') so the hero/
                lifestyle prompts can be made honest about the actual deliverable.
                None uses the generic photography prompts.
            content_context: Real content of the deliverable used to ground the
                prompt (e.g. the actual generated PDF page topics for
                pdf_planner_or_guide), so marketing images depict real content
                rather than an invented cover. Ignored by formats that don't use it.

        Returns:
            Dict with paths to saved images:
              {'hero': Path, 'lifestyle': Path}
        """
        hero_prompt = self._build_hero_prompt(product_name, visual_brief, product_format, content_context)
        lifestyle_prompt = self._build_lifestyle_prompt(product_name, visual_brief, product_format, content_context)

        hero_result = asyncio.run(
            self.image_provider.generate_image(
                hero_prompt, aspect_ratio=aspect_ratio, resolution=resolution
            )
        )
        hero_path = self.file_service.save_from_result(
            hero_result, task_id, "listing", "hero.png"
        )

        lifestyle_result = asyncio.run(
            self.image_provider.generate_image(
                lifestyle_prompt, aspect_ratio=aspect_ratio, resolution=resolution
            )
        )
        lifestyle_path = self.file_service.save_from_result(
            lifestyle_result, task_id, "listing", "lifestyle.png"
        )

        self.log_service.info(
            source="ProductImageAgent",
            message="Listing images generated",
            payload={
                "task_id": task_id,
                "product_name": product_name,
                "hero": str(hero_path),
                "lifestyle": str(lifestyle_path),
            },
        )

        return {"hero": hero_path, "lifestyle": lifestyle_path}

    def _build_marketing_prompt(self, role: str, product_name: str, visual_brief: str) -> str:
        """Generic listing-image prompt for any role beyond hero/lifestyle, so a
        product_format that adds extra marketing shots (an in-use/coloring shot,
        a detail crop, etc.) can still be regenerated by role rather than being
        forced into the hero prompt."""
        label = (role or "product").replace("_", " ").strip() or "product"
        return (
            f"Marketing/listing photograph ({label} view) of: {product_name}. "
            f"Visual brief: {visual_brief}. "
            "Clean, high-quality image suitable for an online marketplace listing. "
            "No text, no watermarks."
        )

    # Role -> prompt-builder. Any role not listed falls back to the generic
    # marketing prompt above, so regeneration is not hardcoded to two roles.
    def _prompt_for_role(self, role: str, product_name: str, visual_brief: str, product_format=None, content_context: str = "") -> str:
        if role == "hero":
            return self._build_hero_prompt(product_name, visual_brief, product_format, content_context)
        if role == "lifestyle":
            return self._build_lifestyle_prompt(product_name, visual_brief, product_format, content_context)
        return self._build_marketing_prompt(role, product_name, visual_brief)

    def regenerate_listing_image(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        role: str,
        corrective_guidance: str,
        filename: str,
        aspect_ratio: str = "1:1",
        resolution: str = "2K",
        product_format=None,
        content_context: str = "",
    ) -> Path:
        """
        Regenerate ONE listing/marketing image by ROLE (hero, lifestyle, or any
        other role a format may add) with explicit corrective guidance appended
        to the prompt, saving over `filename` so the existing path/catalog entry
        stays stable.

        Used by the marketing/deliverable consistency gate to fix only a
        mismatched image (feeding the vision model's own mismatch description
        back into the prompt) instead of regenerating everything. `role` is no
        longer restricted to hero/lifestyle — an unknown role gets a generic
        marketing prompt so ANY image in the listing set is regenerable.
        `product_format` (+ `content_context`, e.g. real PDF page topics) steer the
        per-format honest framing so a remake converges instead of fighting the
        consistency gate.
        """
        base = self._prompt_for_role(role, product_name, visual_brief, product_format, content_context)
        # The corrective guidance carries the ground-truth design description and
        # the vision model's rejection reason — it MUST reach the generation
        # prompt for the remake to actually steer away from the wrong design.
        prompt = f"{base}\n\n{corrective_guidance}".strip()

        result = asyncio.run(
            self.image_provider.generate_image(
                prompt, aspect_ratio=aspect_ratio, resolution=resolution
            )
        )
        path = self.file_service.save_from_result(result, task_id, "listing", filename)

        self.log_service.info(
            source="ProductImageAgent",
            message="Listing image regenerated with corrective guidance",
            payload={
                "task_id": task_id,
                "role": role,
                "filename": filename,
                "path": str(path),
            },
        )
        return path

    def run(self, task: dict) -> dict:
        """
        Standardized entry point.
        Expected task keys: task_id, product_name, visual_brief,
                            aspect_ratio (optional, default '1:1'),
                            resolution (optional, default '1K').
        """
        return self.generate_listing_images(
            task_id=task.get("task_id", "unknown"),
            product_name=task.get("product_name", ""),
            visual_brief=task.get("visual_brief", ""),
            aspect_ratio=task.get("aspect_ratio", "1:1"),
            resolution=task.get("resolution", "2K"),
            product_format=task.get("product_format"),
            content_context=task.get("content_context", ""),
        )
