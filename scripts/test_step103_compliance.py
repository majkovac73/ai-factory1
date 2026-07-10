"""
Step 103 / C-2 test — Etsy compliance: production partner declared for POD,
AI-assisted disclosure in the payload path.

  [1] create_draft_listing includes production_partner_ids on a POD (physical)
      listing when ETSY_PRODUCTION_PARTNER_ID is set.
  [2] it does NOT send production_partner_ids on a digital download.
  [3] it does NOT send them when the env is unset.

(The description AI-disclosure append is exercised in the orchestrator; here we
verify the payload-level partner declaration, which is the compliance-critical
piece.)

Usage: python scripts/test_step103_compliance.py
"""
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
import app.services.etsy_client as ec

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


settings.ETSY_API_KEY = "k"
settings.ETSY_SHARED_SECRET = "s"
settings.ETSY_SHOP_ID = "shop-1"


class _Resp:
    status_code = 200
    text = ""
    def json(self): return {"listing_id": 1}


class _Client:
    captured = None
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, headers=None, json=None):
        _Client.captured = json
        return _Resp()


async def create(listing):
    with patch.object(ec, "get_valid_access_token", new=AsyncMock(return_value="tok")), \
         patch.object(ec.httpx, "AsyncClient", _Client):
        await ec.EtsyClient().create_draft_listing(listing)
    return _Client.captured


# [1] POD physical with partner set
with patch.object(settings, "ETSY_PRODUCTION_PARTNER_ID", "12345"):
    payload = asyncio.run(create({
        "title": "Funny Cat Tee", "price": 25.0, "type": "physical",
        "shipping_profile_id": "sp-1", "when_made": "made_to_order",
    }))
check("1 POD listing declares production_partner_ids", payload.get("production_partner_ids") == [12345])

# [2] digital download never declares a partner
with patch.object(settings, "ETSY_PRODUCTION_PARTNER_ID", "12345"):
    payload2 = asyncio.run(create({
        "title": "Wall Art", "price": 5.0, "type": "download",
    }))
check("2 digital download has no production_partner_ids", "production_partner_ids" not in payload2)

# [3] unset -> not sent even for POD
with patch.object(settings, "ETSY_PRODUCTION_PARTNER_ID", None):
    payload3 = asyncio.run(create({
        "title": "Funny Cat Tee", "price": 25.0, "type": "physical",
        "shipping_profile_id": "sp-1", "when_made": "made_to_order",
    }))
check("3 unset partner -> not sent", "production_partner_ids" not in payload3)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 compliance tests passed.")
