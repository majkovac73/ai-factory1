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

    def _build_hero_prompt(self, product_name: str, visual_brief: str) -> str:
        return (
            f"Professional product photography style. Hero shot of: {product_name}. "
            f"Visual brief: {visual_brief}. "
            "Clean, high-quality image suitable for an online marketplace listing. "
            "No text, no watermarks, no borders."
        )

    def _build_lifestyle_prompt(self, product_name: str, visual_brief: str) -> str:
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
        resolution: str = "1K",
    ) -> dict:
        """
        Generate hero + lifestyle images and save both as 'listing' variants.

        Args:
            task_id: Task identifier (used as storage subdirectory).
            product_name: Name/type of the product being listed.
            visual_brief: Output from VisualDirectorAgent (text description).
            size: Ignored; kept for API compatibility. OpenRouter uses aspect_ratio+resolution.
            aspect_ratio: OpenRouter aspect ratio string (default '1:1' for Etsy square).
            resolution: OpenRouter resolution tier (default '1K' ≈ 1024px).

        Returns:
            Dict with paths to saved images:
              {'hero': Path, 'lifestyle': Path}
        """
        hero_prompt = self._build_hero_prompt(product_name, visual_brief)
        lifestyle_prompt = self._build_lifestyle_prompt(product_name, visual_brief)

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
            resolution=task.get("resolution", "1K"),
        )
