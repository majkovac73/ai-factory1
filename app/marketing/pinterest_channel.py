import asyncio
import httpx

from app.marketing.base import MarketingChannel
from app.services.pinterest_oauth import get_valid_access_token, api_base
from config import settings


class PinterestChannel(MarketingChannel):
    """
    Marketing channel implementation for Pinterest: creates a Pin
    linking back to an Etsy listing, using the listing's title,
    description, and (if present) an image URL.
    """

    name = "pinterest"

    def post(self, listing: dict) -> dict:
        return asyncio.run(self._post_async(listing))

    async def _post_async(self, listing: dict) -> dict:
        try:
            access_token = await get_valid_access_token()
        except Exception as e:
            return {"success": False, "external_id": None, "url": None, "error": str(e)}

        # A-9: route the pin to the board for this product's format when mapped.
        fmt = listing.get("product_format") or listing.get("type")
        board_id = (getattr(settings, "PINTEREST_BOARD_MAP", None) or {}).get(fmt) or settings.PINTEREST_BOARD_ID

        payload = {
            "board_id": board_id,
            "title": listing.get("title", "")[:100],
            "description": listing.get("description", "")[:500],
            "link": listing.get("listing_url") or listing.get("product_url") or "",
        }

        image_url = listing.get("image_url")
        image_b64 = listing.get("image_base64")
        if image_b64:
            payload["media_source"] = {
                "source_type": "image_base64",
                "content_type": listing.get("image_content_type", "image/png"),
                "data": image_b64,
            }
        elif image_url:
            payload["media_source"] = {"source_type": "image_url", "url": image_url}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{api_base()}/pins",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            return {
                "success": True,
                "external_id": data.get("id"),
                "url": data.get("link") or f"https://www.pinterest.com/pin/{data.get('id')}",
                "error": None,
            }
        except Exception as e:
            return {"success": False, "external_id": None, "url": None, "error": str(e)}