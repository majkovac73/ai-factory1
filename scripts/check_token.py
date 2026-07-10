import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import asyncio
import httpx
from app.services.etsy_oauth import get_valid_access_token
from config import settings

async def test():
    try:
        token = await get_valid_access_token()
        print(f"✓ Token: {token[:30]}...")

        # Etsy requires keystring:shared_secret in x-api-key
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        headers = {
            "Authorization": f"Bearer {token}",
            "x-api-key": api_key_header,
        }

        # Test 1: GET shop info
        print("\n[TEST 1] GET shop info...")
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://openapi.etsy.com/v3/application/shops/{settings.ETSY_SHOP_ID}",
                headers=headers
            )
            print(f"  Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"  ✓ Shop name: {data.get('shop_name')}")
            else:
                print(f"  ✗ Error: {response.text}")

        # Test 2: POST a listing
        print("\n[TEST 2] POST a test listing...")
        async with httpx.AsyncClient() as client:
            payload = {
                "quantity": 1,
                "title": "Test Listing from API",
                "description": "This is a test listing to verify the token has write permissions for the Etsy shop",
                "price": 25.00,
                "who_made": "i_did",
                "when_made": "made_to_order",
                "taxonomy_id": 1,
            }

            response = await client.post(
                f"https://openapi.etsy.com/v3/application/shops/{settings.ETSY_SHOP_ID}/listings",
                headers=headers,
                json=payload
            )
            print(f"  Status: {response.status_code}")
            if response.status_code in [200, 201]:
                print(f"  ✓ Listing created successfully!")
                data = response.json()
                print(f"  Listing ID: {data.get('listing_id')}")
            else:
                print(f"  ✗ Error: {response.text}")

    except Exception as e:
        print(f"✗ Exception: {e}")

asyncio.run(test())