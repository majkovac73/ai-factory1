"""
POD Design Generation Agent — step 71.

Generates the actual sellable design artifact. This image serves DOUBLE DUTY:

  (a) For DIGITAL DOWNLOADS: this IS the product the customer receives
      (a planner page, printable art print, template, checklist, wall art).
      Stored as the 'delivery' variant so it's what Etsy delivers to the buyer.

  (b) For PRINT-ON-DEMAND PHYSICAL GOODS: this is the design that a future
      POD service integration (step 81) will submit to a print fulfillment
      provider for printing on a physical product (mug, shirt, etc.).
      Also stored as the 'delivery' variant so step 81 can find it without
      rework.

The image is generated at the highest quality DALL-E 3 supports (1024x1024
square, which is print-safe at reasonable output sizes). The prompt is
constructed to produce a clean, print-ready design with transparent-bg
intent (DALL-E 3 can't guarantee alpha channel, but the prompt guides it
toward artwork-style output rather than a lifestyle photo).
"""
import asyncio
from pathlib import Path
from typing import Optional

from app.agents.base_agent import BaseAgent
from app.core.providers.image_manager import ImageProviderManager
from app.services.image_file_service import ImageFileService
from config import settings

PRODUCT_TYPES = {
    "digital_download": "printable digital download",
    "pod": "print-on-demand merchandise design",
}


class PODDesignAgent(BaseAgent):
    """
    Generates the delivery-ready design artifact for digital downloads
    and POD products.

    Saves as the 'delivery' variant so:
      - Etsy's digital-file upload endpoint (step 73) can attach it
      - The future POD service (step 81) can pick it up for fulfillment
    """

    def __init__(self, provider=None, model: str = None, image_provider=None):
        super().__init__(provider, model)
        self.image_provider = image_provider or ImageProviderManager.get_provider()
        self.file_service = ImageFileService()

    def _build_design_prompt(
        self,
        product_name: str,
        visual_brief: str,
        product_type: str = "digital_download",
    ) -> str:
        type_label = PRODUCT_TYPES.get(product_type, "print-ready artwork")
        return (
            f"High-quality {type_label}. Design for: {product_name}. "
            f"Visual direction: {visual_brief}. "
            "Artwork style, clean design suitable for printing. "
            "Centered composition, no bleed required, white or transparent background. "
            "Professional quality, no text unless it is integral to the design. "
            "No borders, no watermarks, no drop shadows outside the design."
        )

    def generate_design(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        product_type: str = "digital_download",
        filename: str = "design.png",
        size: str = None,
        aspect_ratio: str = "1:1",
        resolution: str = "2K",
    ) -> Path:
        """
        Generate and save the delivery-ready design.

        Args:
            task_id: Task identifier (storage subdirectory).
            product_name: Name/type of the product.
            visual_brief: Output from VisualDirectorAgent.
            product_type: 'digital_download' or 'pod'.
            filename: Override saved filename.
            size: Ignored; kept for API compatibility.
            aspect_ratio: OpenRouter aspect ratio (default '1:1' for print-safe square).
            resolution: OpenRouter resolution tier (default '2K' for delivery quality).

        Returns:
            Path to the saved delivery-variant file.
        """
        prompt = self._build_design_prompt(product_name, visual_brief, product_type)
        result = asyncio.run(
            self.image_provider.generate_image(
                prompt, aspect_ratio=aspect_ratio, resolution=resolution
            )
        )
        path = self.file_service.save_from_result(result, task_id, "delivery", filename)

        self.log_service.info(
            source="PODDesignAgent",
            message="Design artifact generated",
            payload={
                "task_id": task_id,
                "product_name": product_name,
                "product_type": product_type,
                "path": str(path),
                "size": size,
            },
        )
        return path

    def run(self, task: dict) -> dict:
        """
        Standardized entry point.
        Expected task keys: task_id, product_name, visual_brief,
                            product_type (optional, default 'digital_download'),
                            filename (optional), aspect_ratio (optional),
                            resolution (optional, default '2K' for delivery quality).
        """
        path = self.generate_design(
            task_id=task.get("task_id", "unknown"),
            product_name=task.get("product_name", ""),
            visual_brief=task.get("visual_brief", ""),
            product_type=task.get("product_type", "digital_download"),
            filename=task.get("filename", "design.png"),
            aspect_ratio=task.get("aspect_ratio", "1:1"),
            resolution=task.get("resolution", "2K"),
        )
        return {
            "design_path": path,
            "product_type": task.get("product_type", "digital_download"),
        }
