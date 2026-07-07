"""
Pinterest image integration — step 74.

Wires the SocialImageAgent (step 70) into the existing MarketingService /
PinterestChannel flow so Pinterest posts include a real generated image.

The existing PinterestChannel.post() already accepts an `image_url` key in the
listing dict (see app/marketing/pinterest_channel.py). This service extends
the pipeline by:
  1. Using SocialImageAgent to generate a pin image for the product
  2. Converting the local file path to a URL the Pinterest API can reach

Because the AI Factory is local-first (no public server), we use Pinterest's
`image_base64` media source when posting — Pinterest's API v5 supports
posting a pin with a base64-encoded image directly, avoiding the need for a
public CDN URL.

MarketingService.post_to_channel() is unchanged; callers just pass the
augmented listing dict with an 'image_base64' key.
"""
import asyncio
import base64
from pathlib import Path
from typing import Optional

from app.agents.image.social_image_agent import SocialImageAgent
from app.core.providers.image_manager import ImageProviderManager


class PinterestImageService:
    """
    Generates and attaches a Pinterest-optimized image to a listing dict
    before it is passed to PinterestChannel.post().
    """

    def __init__(self, image_provider=None):
        self.image_provider = image_provider or ImageProviderManager.get_provider()

    def generate_pin_image(
        self,
        task_id: str,
        product_name: str,
        visual_brief: str,
        listing_url: Optional[str] = None,
    ) -> Path:
        """
        Generate a Pinterest pin image using SocialImageAgent.

        Args:
            task_id: Task identifier (used as storage subdirectory).
            product_name: Product name for the pin.
            visual_brief: Visual direction text from VisualDirectorAgent.
            listing_url: Optional Etsy listing URL.

        Returns:
            Local Path to the generated pin image.
        """
        agent = SocialImageAgent(image_provider=self.image_provider)
        path = agent.generate_pin_image(
            task_id=task_id,
            product_name=product_name,
            visual_brief=visual_brief,
            listing_url=listing_url,
        )
        return path

    def enrich_listing_with_image(
        self,
        listing: dict,
        task_id: str,
        visual_brief: str,
    ) -> dict:
        """
        Add a base64-encoded pin image to a listing dict so PinterestChannel
        can post it with a real image attached.

        Pinterest API v5 supports media_source.source_type = 'image_base64',
        so we encode the local file and embed it directly.

        Args:
            listing: Listing dict (from ListingGeneratorAgent or similar).
            task_id: Task identifier.
            visual_brief: Visual direction for image generation.

        Returns:
            A copy of the listing dict with 'image_base64' and 'image_content_type'
            keys added. PinterestChannel.post() will use these if present.
        """
        product_name = listing.get("product_name") or listing.get("title", "")
        listing_url = listing.get("listing_url") or listing.get("product_url")

        pin_path = self.generate_pin_image(
            task_id=task_id,
            product_name=product_name,
            visual_brief=visual_brief,
            listing_url=listing_url,
        )

        b64_data = base64.b64encode(pin_path.read_bytes()).decode()
        enriched = dict(listing)
        enriched["image_base64"] = b64_data
        enriched["image_content_type"] = "image/png"
        enriched["pin_image_path"] = str(pin_path)
        return enriched
