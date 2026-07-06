import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from app.services.etsy_oauth import get_valid_access_token
from config import settings


async def main():
    access_token = await get_valid_access_token()
    api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

    payload = {
        "readiness_state": "made_to_order",
        "processing_min": 3,
        "processing_max": 5,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://openapi.etsy.com/v3/application/shops/{settings.ETSY_SHOP_ID}/readiness-state-definitions",
            headers={
                "Authorization": f"Bearer {access_token}",
                "x-api-key": api_key_header,
            },
            json=payload,
        )
        print(f"Status: {response.status_code}")
        print(response.text)


asyncio.run(main())