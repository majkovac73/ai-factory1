"""
Step 103 / A-9 + B-7 test — Pinterest board routing + Etsy shop sections.

  [1] EtsyClient sends shop_section_id when the listing carries one, omits it otherwise.
  [2] PinterestChannel picks the per-format board from PINTEREST_BOARD_MAP,
      falling back to PINTEREST_BOARD_ID.

Usage: python scripts/test_step103_sections_boards.py
"""
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
import app.services.etsy_client as ec
import app.marketing.pinterest_channel as pc

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


settings.ETSY_API_KEY = "k"; settings.ETSY_SHARED_SECRET = "s"; settings.ETSY_SHOP_ID = "shop"


class _Resp:
    status_code = 200; text = ""
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
    with patch.object(ec, "get_valid_access_token", new=AsyncMock(return_value="t")), \
         patch.object(ec.httpx, "AsyncClient", _Client):
        await ec.EtsyClient().create_draft_listing(listing)
    return _Client.captured


# [1] shop_section_id
p = asyncio.run(create({"title": "x", "price": 5.0, "type": "download", "shop_section_id": "789"}))
check("1 shop_section_id sent when present", p.get("shop_section_id") == 789)
p2 = asyncio.run(create({"title": "x", "price": 5.0, "type": "download"}))
check("1 shop_section_id omitted when absent", "shop_section_id" not in p2)

# [2] Pinterest board routing
class _PinResp:
    status_code = 201
    def json(self): return {"id": "pin1"}

class _PinClient:
    captured = None
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, headers=None, json=None):
        _PinClient.captured = json
        return _PinResp()

def post_pin(listing):
    with patch.object(pc, "get_valid_access_token", new=AsyncMock(return_value="t")), \
         patch.object(pc.httpx, "AsyncClient", _PinClient):
        pc.PinterestChannel().post(listing)
    return _PinClient.captured

with patch.object(settings, "PINTEREST_BOARD_MAP", {"single_print": "BOARD_ART"}), \
     patch.object(settings, "PINTEREST_BOARD_ID", "BOARD_DEFAULT"):
    cap = post_pin({"title": "t", "description": "d", "product_format": "single_print", "image_base64": "x"})
    check("2 mapped format uses its board", cap.get("board_id") == "BOARD_ART")
    cap2 = post_pin({"title": "t", "description": "d", "product_format": "coloring_page", "image_base64": "x"})
    check("2 unmapped format falls back to default board", cap2.get("board_id") == "BOARD_DEFAULT")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 sections/boards tests passed.")
