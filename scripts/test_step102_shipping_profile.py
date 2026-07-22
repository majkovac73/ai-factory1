"""
EtsyShippingService: pick a usable WORLDWIDE physical shipping profile.

Uses Etsy's REAL field names (shipping_profile_id / profile_type — the old code
read profile_id/type and never matched anything) and requires the profile to ship
beyond the seller's own country (region eu/non_eu or an explicit destination),
preferring the profile this service auto-creates ("POD Standard Shipping").

Usage: python scripts/test_step102_shipping_profile.py
"""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
import app.services.etsy_shipping_service as ess

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


settings.ETSY_SHOP_ID = "shop-1"
settings.ETSY_API_KEY = "k"
settings.ETSY_SHARED_SECRET = "s"


def prof(pid, title="P", profile_type="manual", is_deleted=False, dests=None):
    return {"shipping_profile_id": pid, "title": title, "profile_type": profile_type,
            "is_deleted": is_deleted, "shipping_profile_destinations": dests or []}


WORLDWIDE = [{"destination_region": "eu"}, {"destination_region": "non_eu"}]
DOMESTIC = [{"destination_region": "none", "destination_country_iso": ""}]


def fetch(profiles):
    ess._cached_profile_id = None
    async def fake_list(self=None): return profiles
    with patch.object(ess.EtsyShippingService, "_list_profiles", fake_list):
        return asyncio.run(ess.EtsyShippingService()._fetch_existing())


# digital first, physical-worldwide second -> skip digital, pick physical worldwide
check("skips digital, picks worldwide physical 2",
      fetch([prof(1, profile_type="digital", dests=WORLDWIDE), prof(2, dests=WORLDWIDE)]) == "2")

# deleted worldwide skipped, live worldwide picked
check("skips deleted, picks live worldwide 10",
      fetch([prof(9, is_deleted=True, dests=WORLDWIDE), prof(10, dests=WORLDWIDE)]) == "10")

# only digital -> None (caller creates a default)
check("only digital -> None (create default)",
      fetch([prof(1, profile_type="digital", dests=WORLDWIDE)]) is None)

# domestic-only skipped, worldwide picked
check("skips domestic-only, picks worldwide 6",
      fetch([prof(5, dests=DOMESTIC), prof(6, dests=WORLDWIDE)]) == "6")

# no worldwide profile at all -> None
check("no worldwide profile -> None", fetch([prof(5, dests=DOMESTIC)]) is None)

# our auto-created title is preferred over another worldwide profile
check("prefers 'POD Standard Shipping' title",
      fetch([prof(7, title="Other", dests=WORLDWIDE), prof(8, title="POD Standard Shipping", dests=WORLDWIDE)]) == "8")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All shipping-profile tests passed.")
