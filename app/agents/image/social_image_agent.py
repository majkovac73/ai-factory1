"""
Social Media Image Generation Agent — step 70.

Generates images sized and composed for Pinterest posts. Pinterest's
optimal pin size is 2:3 portrait ratio.

OpenRouter's google/gemini-3.1-flash-image natively supports aspect_ratio="2:3",
replacing the previous DALL-E 3 workaround of size="1024x1792" (which was
a 4:7 ratio approximation, not a true 2:3). The model is asked for 2:3 directly.

These images are distinct from Etsy listing images (step 69):
  - Different aspect ratio (2:3 portrait vs 1:1 square)
  - Framed to be scroll-stopping in a Pinterest feed rather than a
    clean product shot in a listing context
  - Should include visual context (mood, setting) that works without
    reading a title

Stored as 'listing' variant (preview-quality — not a customer deliverable).
"""
import asyncio
from pathlib import Path
from typing import Optional

from app.agents.base_agent import BaseAgent
from app.core.providers.image_manager import ImageProviderManager
from app.services.image_file_service import ImageFileService

PINTEREST_ASPECT_RATIO = "2:3"
PINTEREST_RESOLUTION = "4K"  # Seedream 4.5: non-square 2:3 at 2K is only ~2.8M pixels, below the 3.69M minimum; 4K satisfies it. Flat-rate so no extra cost.


class SocialImageAgent(BaseAgent):
    """
    Generates Pinterest-optimized images for a product.
    Produces one tall portrait pin image per call using native 2:3 aspect ratio.
    """

    def __init__(self, provider=None, model: str = None, image_provider=None):
        super().__init__(provider, model)
        self.image_provider = image_provider or ImageProviderManager.get_provider()
        self.file_service = ImageFileService()

    def _build_pin_prompt(
        self, product_name: str, visual_brief: str, listing_url: Optional[str] = None
    ) -> str:
        url_hint = f" Link destination: {listing_url}." if listing_url else ""
        return (
            f"Pinterest pin image, tall portrait 2:3 ratio. Product: {product_name}. "
            f"Visual brief: {visual_brief}. "
            "Scroll-stopping composition, aspirational mood, clean aesthetic. "
            "Leave visual breathing room at the top for a title overlay if needed. "
            f"No text, no watermarks.{url_hint}"
        )

    def generate_pin_image(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        listing_url: Optional[str] = None,
        filename: str = "pin.png",
        aspect_ratio: str = PINTEREST_ASPECT_RATIO,
        resolution: str = PINTEREST_RESOLUTION,
    ) -> Path:
        """
        Generate a single Pinterest pin image and save it as a 'listing' variant.

        Args:
            task_id: Task identifier (storage subdirectory).
            product_name: Product being promoted.
            visual_brief: Output from VisualDirectorAgent.
            listing_url: Optional Etsy listing URL to inform composition.
            filename: Override saved filename.
            aspect_ratio: OpenRouter aspect ratio (default '2:3' for Pinterest portrait).
            resolution: OpenRouter resolution tier (default '1K').

        Returns:
            Path to the saved pin image.
        """
        prompt = self._build_pin_prompt(product_name, visual_brief, listing_url)
        result = asyncio.run(
            self.image_provider.generate_image(
                prompt, aspect_ratio=aspect_ratio, resolution=resolution
            )
        )
        path = self.file_service.save_from_result(result, task_id, "listing", filename)

        self.log_service.info(
            source="SocialImageAgent",
            message="Pinterest pin image generated",
            payload={
                "task_id": task_id,
                "product_name": product_name,
                "path": str(path),
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            },
        )
        return path

    def run(self, task: dict) -> dict:
        """
        Standardized entry point.
        Expected task keys: task_id, product_name, visual_brief,
                            listing_url (optional), filename (optional),
                            aspect_ratio (optional), resolution (optional).
        """
        path = self.generate_pin_image(
            task_id=task.get("task_id", "unknown"),
            product_name=task.get("product_name", ""),
            visual_brief=task.get("visual_brief", ""),
            listing_url=task.get("listing_url"),
            filename=task.get("filename", "pin.png"),
            aspect_ratio=task.get("aspect_ratio", PINTEREST_ASPECT_RATIO),
            resolution=task.get("resolution", PINTEREST_RESOLUTION),
        )
        return {"pin_image": path}
