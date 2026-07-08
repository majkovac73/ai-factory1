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
            "tags": listing.get("tags", [])[:13],
            "materials": listing.get("materials", [])[:13],
        }

        # Only include optional fields when explicitly provided — sending null
        # shipping_profile_id causes 422 on physical listings; sending no type
        # field defaults to physical which then requires a shipping profile.
        if listing.get("type"):
            payload["type"] = listing["type"]
        if listing.get("shipping_profile_id"):
            payload["shipping_profile_id"] = listing["shipping_profile_id"]
        if listing.get("readiness_state_id"):
            payload["readiness_state_id"] = listing["readiness_state_id"]

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

    async def delete_listing(self, listing_id: str) -> bool:
        """
        Delete a listing outright. Used by PipelineOrchestrator's hard product
        gate (step 90) to remove a draft listing that was created but turned
        out to have no verified product/file behind it — a listing with
        nothing real behind it is worse than no listing at all.

        Etsy endpoint: DELETE /v3/application/listings/{listing_id}
        """
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{ETSY_API_BASE}/listings/{listing_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
            )

            if response.status_code >= 400:
                raise Exception(f"Etsy API error {response.status_code}: {response.text}")

            return True