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
        content_type = listing.get("image_content_type", "image/png")
        # The refresh/backfill path supplies image_PATH (a local file), not base64
        # — a Pin with no media_source is a 400. Read + base64-encode the file here.
        if not image_b64 and not image_url:
            image_path = listing.get("image_path")
            if image_path:
                try:
                    import base64 as _b64
                    import pathlib
                    p = pathlib.Path(image_path)
                    if p.exists() and p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                        image_b64 = _b64.b64encode(p.read_bytes()).decode()
                        content_type = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                except Exception:
                    pass

        if image_b64:
            payload["media_source"] = {
                "source_type": "image_base64",
                "content_type": content_type,
                "data": image_b64,
            }
        elif image_url:
            payload["media_source"] = {"source_type": "image_url", "url": image_url}

        if "media_source" not in payload:
            return {"success": False, "external_id": None, "url": None,
                    "error": "no usable image (Pinterest requires an image; provide a PNG/JPG "
                             "image_path, image_base64, or image_url)"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{api_base()}/pins",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                )
                if response.status_code >= 400:
                    # surface Pinterest's real reason, not a generic httpx string.
                    return {"success": False, "external_id": None, "url": None,
                            "error": f"{response.status_code}: {response.text[:500]}"}
                data = response.json()

            return {
                "success": True,
                "external_id": data.get("id"),
                "url": data.get("link") or f"https://www.pinterest.com/pin/{data.get('id')}",
                "error": None,
            }
        except Exception as e:
            return {"success": False, "external_id": None, "url": None, "error": str(e)}