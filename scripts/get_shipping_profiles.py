import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from app.services.etsy_oauth import get_valid_access_token
from config import settings


async def main():
    access_token = await get_valid_access_token()

    async with httpx.AsyncClient() as client:
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        response = await client.get(
            f"https://openapi.etsy.com/v3/application/shops/{settings.ETSY_SHOP_ID}/shipping-profiles",
            headers={
                "Authorization": f"Bearer {access_token}",
                "x-api-key": api_key_header,
            },
        )

        if response.status_code != 200:
            print(f"Status: {response.status_code}")
            print(f"Body: {response.text}")
            return

        data = response.json()

    print("Shipping profiles on your shop:")
    for profile in data.get("results", []):
        print(f"  id={profile['shipping_profile_id']}  title={profile['title']}")


asyncio.run(main())