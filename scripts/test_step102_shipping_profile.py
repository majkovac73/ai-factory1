"""
Step 102 / P1-7 test — EtsyShippingService picks a PHYSICAL profile, skipping
digital/unsuitable ones (the old code took the first profile of any type).

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


class _Resp:
    status_code = 200
    def __init__(self, data): self._d = data
    def json(self): return self._d
    text = ""


class _Client:
    _data = None
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return _Resp(_Client._data)


def run_with_profiles(profiles):
    ess._cached_profile_id = None  # reset module cache
    _Client._data = {"results": profiles}
    with patch.object(ess.httpx, "AsyncClient", _Client), \
         patch.object(ess, "get_valid_access_token", side_effect=lambda: _await_token()):
        return asyncio.run(ess.EtsyShippingService()._fetch_existing())


async def _await_token():
    return "token"


# digital first, physical second -> must skip digital, pick physical
picked = run_with_profiles([
    {"profile_id": 1, "type": "digital", "is_deleted": False},
    {"profile_id": 2, "type": "manual", "is_deleted": False, "min_processing_time": 3, "title": "Standard"},
])
check("skips digital, picks physical profile 2", picked == "2")

# deleted physical skipped
picked2 = run_with_profiles([
    {"profile_id": 9, "type": "manual", "is_deleted": True, "min_processing_time": 3},
    {"profile_id": 10, "type": "manual", "is_deleted": False, "min_processing_time": 5},
])
check("skips deleted, picks live physical profile 10", picked2 == "10")

# only digital -> None (so caller creates a default)
picked3 = run_with_profiles([
    {"profile_id": 1, "type": "digital", "is_deleted": False},
])
check("only digital available -> None (create default)", picked3 is None)

# profile without processing time skipped
picked4 = run_with_profiles([
    {"profile_id": 5, "type": "manual", "is_deleted": False},  # no min_processing_time
    {"profile_id": 6, "type": "manual", "is_deleted": False, "min_processing_time": 2},
])
check("skips profile lacking processing time, picks 6", picked4 == "6")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 shipping-profile tests passed.")
