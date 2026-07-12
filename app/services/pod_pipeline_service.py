"""
POD product pipeline integration — step 75.

Wires PODDesignAgent (step 71) into the product pipeline for POD-flagged
products. The generated design is stored as the 'delivery' variant so that:

  - For digital downloads: EtsyImageService (step 73) can attach it as the
    file the buyer downloads immediately after purchase.
  - For physical POD products: step 81 (future) can retrieve it from the
    delivery path and submit it to a print fulfillment service.

No actual POD service API integration is performed here — that's step 81.
This service only ensures the design artifact is generated and stored in the
right location for step 81 to consume without rework.

Product type routing:
  product_type == 'digital_download' → generate design, store as delivery
  product_type == 'pod'              → generate design, store as delivery
  anything else                      → no-op (returns None)
"""
from pathlib import Path
from typing import Optional

from app.agents.image.pod_design_agent import PODDesignAgent
from app.core.providers.image_manager import ImageProviderManager


class PODPipelineService:
    """
    Coordinates POD design generation for the product pipeline.
    Stores results as the 'delivery' variant so step 81 can consume them.
    """

    SUPPORTED_TYPES = {"digital_download", "pod"}

    def __init__(self, image_provider=None):
        self.image_provider = image_provider or ImageProviderManager.get_provider()

    def generate_and_store_design(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        product_type: str = "digital_download",
        filename: str = "design.png",
        aspect_ratio: str = "1:1",
        resolution: str = "2K",
    ) -> Optional[Path]:
        """
        Generate and store the delivery-ready design artifact for a product.

        Args:
            task_id: Task identifier (maps to data/images/delivery/{task_id}/).
            product_name: Name of the product being designed.
            visual_brief: Visual direction from VisualDirectorAgent.
            product_type: 'digital_download' or 'pod'.
            filename: Override filename for the design file.

        Returns:
            Path to the saved design file, or None if product_type is unsupported.
        """
        if product_type not in self.SUPPORTED_TYPES:
            return None

        agent = PODDesignAgent(image_provider=self.image_provider)
        result = agent.run({
            "task_id": task_id,
            "product_name": product_name,
            "visual_brief": visual_brief,
            "product_type": product_type,
            "filename": filename,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        })
        return result["design_path"]

    def build_product_record(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        product_type: str = "digital_download",
        aspect_ratio: str = "1:1",
        resolution: str = "2K",
        filename: str = "design.png",
    ) -> dict:
        """
        High-level entry point: generate the design and return a product record
        that downstream steps (73, 81) can use.

        `filename` (105 1-3) lets a caller store the design under a distinct
        name — the wall-art set generates 3 pieces for ONE task and must not have
        each overwrite `design.png`.

        Returns:
            Dict with task_id, product_type, design_path (str or None),
            and ready_for_pod flag.
        """
        design_path = self.generate_and_store_design(
            task_id=task_id,
            product_name=product_name,
            visual_brief=visual_brief,
            product_type=product_type,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            filename=filename,
        )
        return {
            "task_id": task_id,
            "product_type": product_type,
            "design_path": str(design_path) if design_path else None,
            "ready_for_pod": design_path is not None,
        }
