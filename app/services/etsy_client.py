import httpx

from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


class EtsyClient:
    """
    Thin wrapper around Etsy's Open API v3 listing creation endpoint.
    """

    async def create_draft_listing(self, listing: dict) -> dict:
        access_token = await get_valid_access_token()

        # Etsy requires BOTH keystring and shared secret in x-api-key header
        # Format: "keystring:shared_secret"
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        payload = {
            "quantity": listing.get("quantity", 1),
            "title": listing.get("title", "")[:140],
            "description": listing.get("description", ""),
            "price": listing.get("price") or 0,
            "who_made": "i_did",
            "when_made": "made_to_order",
            "taxonomy_id": listing.get("taxonomy_id", 1),
            "shipping_profile_id": listing.get("shipping_profile_id"),
            "tags": listing.get("tags", [])[:13],
            "materials": listing.get("materials", [])[:13],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
                json=payload,
            )

            if response.status_code >= 400:
                raise Exception(f"Etsy API error {response.status_code}: {response.text}")

            return response.json()