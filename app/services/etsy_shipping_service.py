"""
EtsyShippingService — get or create a reusable shipping profile.

Priority:
  1. settings.ETSY_SHIPPING_PROFILE_ID — use it directly (no API call)
  2. GET existing profiles from the shop — pick the first physical one
  3. POST a new profile with sensible defaults and log the ID

Once an ID is found or created it is cached in-process so subsequent
_stage_create_listing() calls within the same server run cost 0 API hits.

Set ETSY_SHIPPING_PROFILE_ID in Railway env vars after the first deploy
to avoid the lookup entirely.
"""
import logging
from typing import Optional

import httpx

from app.services.etsy_oauth import get_valid_access_token
from config import settings

logger = logging.getLogger("ai-factory")

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"

_cached_profile_id: Optional[str] = None


class EtsyShippingService:

    async def get_or_create(self) -> Optional[str]:
        """
        Return a shipping_profile_id suitable for a physical POD listing.
        Returns None if the shop is not configured (no ETSY_SHOP_ID).
        """
        global _cached_profile_id

        if _cached_profile_id:
            return _cached_profile_id

        # Fast path: explicitly configured in env
        if settings.ETSY_SHIPPING_PROFILE_ID:
            _cached_profile_id = settings.ETSY_SHIPPING_PROFILE_ID
            logger.info(f"EtsyShippingService: using configured profile {_cached_profile_id}")
            return _cached_profile_id

        if not settings.ETSY_SHOP_ID:
            logger.warning("EtsyShippingService: ETSY_SHOP_ID not set, cannot resolve shipping profile")
            return None

        try:
            profile_id = await self._fetch_existing()
            if not profile_id:
                profile_id = await self._create_default()
            if profile_id:
                _cached_profile_id = profile_id
                logger.info(
                    f"EtsyShippingService: resolved shipping profile {profile_id} — "
                    f"set ETSY_SHIPPING_PROFILE_ID={profile_id} in env to skip future lookups"
                )
            return profile_id
        except Exception as e:
            logger.error(f"EtsyShippingService: failed to resolve shipping profile: {e}")
            return None

    async def _fetch_existing(self) -> Optional[str]:
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/shipping-profiles",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
            )
            if resp.status_code >= 400:
                logger.warning(f"EtsyShippingService: GET shipping-profiles returned {resp.status_code}: {resp.text}")
                return None

            data = resp.json()
            profiles = data.get("results", [])
            # P1-7: actually skip digital/unsuitable profiles (the old code took
            # the FIRST profile of ANY type despite the comment). Attaching a
            # digital or wrong profile to a physical POD listing breaks checkout
            # shipping. Require a real physical profile: not deleted, not the
            # digital auto-profile, and carrying a processing time.
            for p in profiles:
                if not p.get("profile_id") or p.get("is_deleted"):
                    continue
                if p.get("type") == "digital":
                    continue
                if not p.get("min_processing_time"):
                    continue
                logger.info(
                    f"EtsyShippingService: selected physical shipping profile "
                    f"{p['profile_id']} ('{p.get('title', '')}')"
                )
                return str(p["profile_id"])
            logger.info("EtsyShippingService: no suitable physical profile found; will create one")
            return None

    async def _create_default(self) -> Optional[str]:
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        payload = {
            "title": "Standard Shipping (auto-created)",
            "origin_country_iso": settings.ETSY_SHOP_ORIGIN_COUNTRY,
            "primary_cost": 5.00,
            "secondary_cost": 2.00,
            "min_processing_time": 3,
            "max_processing_time": 7,
            "processing_time_unit": "business_days",
            "destination_region": "everywhere",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/shipping-profiles",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
                json=payload,
            )
            if resp.status_code >= 400:
                logger.error(f"EtsyShippingService: POST shipping-profiles failed {resp.status_code}: {resp.text}")
                return None

            data = resp.json()
            profile_id = str(data.get("shipping_profile_id", ""))
            logger.info(f"EtsyShippingService: created shipping profile {profile_id}")
            return profile_id or None
