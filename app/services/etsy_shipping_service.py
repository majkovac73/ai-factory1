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

    # Title of the profile this service auto-creates (also used to find it again
    # so we never create duplicates across restarts).
    AUTO_TITLE = "POD Standard Shipping"

    async def _list_profiles(self) -> list:
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/shipping-profiles",
                headers={"Authorization": f"Bearer {access_token}", "x-api-key": api_key_header},
            )
            if resp.status_code >= 400:
                logger.warning(f"EtsyShippingService: GET shipping-profiles {resp.status_code}: {resp.text[:200]}")
                return []
            return resp.json().get("results", []) or []

    @staticmethod
    def _ships_beyond_domestic(p: dict) -> bool:
        """True if the profile has any destination beyond the seller's own country
        (region eu/non_eu, or an explicit destination country). A domestic-only
        profile ('none' region, empty country) can't sell to a worldwide POD
        audience, so it isn't a suitable default."""
        for d in p.get("shipping_profile_destinations", []) or []:
            if d.get("destination_region") in ("eu", "non_eu") or d.get("destination_country_iso"):
                return True
        return False

    async def _fetch_existing(self) -> Optional[str]:
        """Find a usable physical shipping profile. Etsy's field is
        `shipping_profile_id` (NOT `profile_id`) and its type is `profile_type` —
        the old code read the wrong keys, so it never matched anything. Prefer the
        profile this service auto-created, then any non-digital profile that ships
        beyond the seller's own country."""
        profiles = await self._list_profiles()
        candidates = [
            p for p in profiles
            if p.get("shipping_profile_id") and not p.get("is_deleted")
            and p.get("profile_type") != "digital"
        ]
        for p in candidates:  # our own auto-created worldwide profile first
            if p.get("title") == self.AUTO_TITLE and self._ships_beyond_domestic(p):
                logger.info(f"EtsyShippingService: using auto profile {p['shipping_profile_id']}")
                return str(p["shipping_profile_id"])
        for p in candidates:  # any other broadly-shipping physical profile
            if self._ships_beyond_domestic(p):
                logger.info(f"EtsyShippingService: using physical profile {p['shipping_profile_id']} ('{p.get('title','')}')")
                return str(p["shipping_profile_id"])
        logger.info("EtsyShippingService: no worldwide physical profile found; will create one")
        return None

    async def _origin(self) -> tuple:
        """(country_iso, postal_code) for creating a profile. Derive from any
        existing profile (self-configuring), else fall back to settings."""
        for p in await self._list_profiles():
            cc, pc = p.get("origin_country_iso"), p.get("origin_postal_code")
            if cc and pc:
                return str(cc), str(pc)
        return settings.ETSY_SHOP_ORIGIN_COUNTRY, (settings.ETSY_SHOP_ORIGIN_POSTAL_CODE or "")

    async def _create_default(self) -> Optional[str]:
        """Create a worldwide POD shipping profile (free shipping — POD price
        includes shipping, and free shipping boosts Etsy ranking). Etsy requires
        origin_postal_code and delivery days; the create call makes the profile +
        an EU destination, then a non_eu destination is added for worldwide."""
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
        H = {"Authorization": f"Bearer {access_token}", "x-api-key": api_key_header}
        country, postal = await self._origin()
        if not country or not postal:
            logger.error("EtsyShippingService: cannot create profile — no origin country/postal "
                         "(set ETSY_SHOP_ORIGIN_COUNTRY + ETSY_SHOP_ORIGIN_POSTAL_CODE).")
            return None

        base = f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/shipping-profiles"
        payload = {
            "title": self.AUTO_TITLE,
            "origin_country_iso": country,
            "origin_postal_code": postal,
            "primary_cost": 0.00, "secondary_cost": 0.00,
            "min_processing_time": 3, "max_processing_time": 7,
            "processing_time_unit": "business_days",
            "destination_region": "eu", "min_delivery_days": 5, "max_delivery_days": 10,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(base, headers=H, json=payload)
            if resp.status_code >= 400:
                logger.error(f"EtsyShippingService: create profile failed {resp.status_code}: {resp.text[:300]}")
                return None
            profile_id = str(resp.json().get("shipping_profile_id", ""))
            if not profile_id:
                return None
            # add worldwide (non-EU) coverage — best-effort; the profile is already usable.
            try:
                await client.post(
                    f"{base}/{profile_id}/destinations", headers=H,
                    json={"destination_region": "non_eu", "primary_cost": 0.00, "secondary_cost": 0.00,
                          "min_delivery_days": 10, "max_delivery_days": 21},
                )
            except Exception as e:
                logger.warning(f"EtsyShippingService: could not add non_eu destination: {e}")
            logger.info(f"EtsyShippingService: created worldwide shipping profile {profile_id}")
            return profile_id
