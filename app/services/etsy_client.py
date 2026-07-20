import httpx

from app.core.http_backoff import request_with_backoff
from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"

# `when_made` for an INSTANT digital download. Must NOT be "made_to_order":
# Etsy treats a made_to_order digital listing as a personalized/custom item
# the seller delivers manually after purchase, so its editor HIDES the
# instant-download file slot even when a file is attached via API — confirmed
# live on listing 4534427807, whose file only became visible in the editor
# after when_made was changed away from made_to_order. "made_to_order" IS
# correct for POD physical goods (printed after purchase), so this only
# applies to digital-download listings. This is a recent-era value from
# Etsy's real when_made enum; if Etsy rolls the enum forward past 2026, this
# constant needs bumping — the create-time when_made readback (step 95) will
# surface it loudly if it ever becomes invalid/rejected.
DIGITAL_WHEN_MADE = "2020_2026"
POD_WHEN_MADE = "made_to_order"


class EtsyClient:
    """
    Thin wrapper around Etsy's Open API v3 listing creation endpoint.
    """

    async def create_draft_listing(self, listing: dict) -> dict:
        access_token = await get_valid_access_token()

        # Etsy requires BOTH keystring and shared secret in x-api-key header
        # Format: "keystring:shared_secret"
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        # when_made defaults to POD's made_to_order for backward-compat, but the
        # orchestrator sets it explicitly per listing type: a real recent-era
        # value for digital downloads (so Etsy shows the instant-download file
        # slot) and made_to_order for POD physical goods.
        # P0-11: refuse to publish a listing with no real price. Etsy's minimum
        # is $0.20; a 0/None price means pricing failed upstream and should
        # block, not silently create a broken listing. The pipeline clamps the
        # price into a per-format band before this, so a valid price is expected.
        price = listing.get("price")
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0.20:
            raise ValueError(f"Refusing to create Etsy listing with invalid price {price!r} (min $0.20)")

        payload = {
            "quantity": listing.get("quantity", 1),
            "title": listing.get("title", "")[:140],
            "description": listing.get("description", ""),
            "price": price,
            "who_made": "i_did",
            "when_made": listing.get("when_made", POD_WHEN_MADE),
            "taxonomy_id": listing.get("taxonomy_id", 1),
            "tags": listing.get("tags", [])[:13],
            "materials": listing.get("materials", [])[:13],
        }

        # B-7: place the listing in its shop section when configured.
        if listing.get("shop_section_id"):
            try:
                payload["shop_section_id"] = int(listing["shop_section_id"])
            except (TypeError, ValueError):
                pass

        # Only include optional fields when explicitly provided — sending null
        # shipping_profile_id causes 422 on physical listings; sending no type
        # field defaults to physical which then requires a shipping profile.
        is_download = listing.get("type") == "download"
        if listing.get("type"):
            payload["type"] = listing["type"]
        # Physical-only fields must NEVER be sent on a digital download —
        # a download listing has no shipping and no made-after-order readiness.
        # Sending them makes Etsy present the listing as a hybrid/physical-ish
        # item. Strip them defensively regardless of what the caller passed.
        if not is_download:
            if listing.get("shipping_profile_id"):
                payload["shipping_profile_id"] = listing["shipping_profile_id"]
            if listing.get("readiness_state_id"):
                payload["readiness_state_id"] = listing["readiness_state_id"]
            # C-2: declare the production partner (Printify) on POD physical
            # listings — required by Etsy's Creativity Standards. Only sent when
            # ETSY_PRODUCTION_PARTNER_ID is configured.
            partner_id = getattr(settings, "ETSY_PRODUCTION_PARTNER_ID", None)
            if partner_id:
                try:
                    payload["production_partner_ids"] = [int(partner_id)]
                except (TypeError, ValueError):
                    pass

        async with httpx.AsyncClient() as client:
            response = await request_with_backoff(  # #12: 429/5xx backoff
                client, "POST",
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

    async def get_production_partners(self) -> dict:
        """List the shop's declared production partners (for POD compliance —
        ETSY_PRODUCTION_PARTNER_ID). Etsy endpoint:
        GET /v3/application/shops/{shop_id}/production-partners. Uses the stored
        OAuth token, so no manual key/token pasting."""
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
        async with httpx.AsyncClient() as client:
            response = await request_with_backoff(  # #12
                client, "GET",
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/production-partners",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
            )
            if response.status_code >= 400:
                raise Exception(f"Etsy API error {response.status_code}: {response.text}")
            return response.json()

    async def get_listing(self, listing_id: str) -> dict:
        """
        Readback verification (step 93): re-fetch a listing to confirm real
        attributes (e.g. taxonomy_id) rather than trusting the create/update
        response alone. Etsy endpoint: GET /v3/application/listings/{listing_id}
        (not shop-scoped — verified live against production, 200 OK).
        """
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        async with httpx.AsyncClient() as client:
            response = await request_with_backoff(  # #12
                client, "GET",
                f"{ETSY_API_BASE}/listings/{listing_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
            )
            if response.status_code >= 400:
                raise Exception(f"Etsy API error {response.status_code}: {response.text}")
            return response.json()

    async def update_listing(self, listing_id: str, fields: dict) -> dict:
        """
        Update arbitrary fields on an existing listing (step 93 — used to
        correct taxonomy_id on listings created before this fix). Etsy
        endpoint: PATCH /v3/application/shops/{shop_id}/listings/{listing_id}
        (shop-scoped — same pattern as EtsyImageService.publish_listing).
        """
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        async with httpx.AsyncClient() as client:
            response = await request_with_backoff(  # #12
                client, "PATCH",
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{listing_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
                json=fields,
            )
            if response.status_code >= 400:
                raise Exception(f"Etsy API error {response.status_code}: {response.text}")
            return response.json()

    async def update_listing_inventory(self, listing_id: str, inventory: dict) -> dict:
        """7-2: set a listing's variations (size/color inventory). Etsy endpoint:
        PUT /v3/application/listings/{listing_id}/inventory (NOT shop-scoped).
        `inventory` is the PodVariantMapper.build_etsy_inventory payload."""
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await request_with_backoff(  # #12
                client, "PUT",
                f"{ETSY_API_BASE}/listings/{listing_id}/inventory",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                    "Content-Type": "application/json",
                },
                json=inventory,
            )
            if response.status_code >= 400:
                raise Exception(f"Etsy inventory error {response.status_code}: {response.text}")
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
            response = await request_with_backoff(  # #12
                client, "DELETE",
                f"{ETSY_API_BASE}/listings/{listing_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
            )

            if response.status_code >= 400:
                raise Exception(f"Etsy API error {response.status_code}: {response.text}")

            return True