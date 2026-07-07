"""
Step 81-2b: Discover the Printify shop_id.

Makes one real, free GET /v1/shops.json call using PRINTIFY_API_KEY.
If exactly one shop is found, prints instructions for adding PRINTIFY_SHOP_ID
to config/settings.py and .env. If multiple shops exist, stops and asks
Maj to specify which to use.
"""
import sys
import httpx
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings

print("=" * 60)
print("STEP 81-2b — DISCOVER PRINTIFY SHOP ID")
print("=" * 60)

api_key = settings.PRINTIFY_API_KEY
if not api_key:
    print("\nERROR: PRINTIFY_API_KEY is not set in .env / settings.")
    sys.exit(1)

print(f"\n[1] Calling GET https://api.printify.com/v1/shops.json ...")
response = httpx.get(
    "https://api.printify.com/v1/shops.json",
    headers={
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "AI-Factory/1.0",
    },
    timeout=15,
)

if response.status_code != 200:
    print(f"ERROR: {response.status_code} {response.text}")
    sys.exit(1)

shops = response.json()
print(f"\n  Shops returned: {len(shops)}")
for shop in shops:
    print(f"    id={shop['id']}  title={shop.get('title', '?')}  channel={shop.get('sales_channel', '?')}")

if len(shops) == 0:
    print("\nNo Printify shops found. Create a shop at printify.com first.")
    sys.exit(1)

if len(shops) > 1:
    print(
        "\nMultiple shops found — cannot auto-select. "
        "Add PRINTIFY_SHOP_ID=<id> to .env for the correct shop and re-run."
    )
    sys.exit(1)

shop = shops[0]
shop_id = shop["id"]
print(f"\n[2] Exactly one shop found: id={shop_id}  title={shop.get('title', '?')}")
print("\nAdd these two lines to your .env file (and config/settings.py if not already present):")
print(f"  PRINTIFY_SHOP_ID={shop_id}")
print("\nconfig/settings.py already has PRINTIFY_SHOP_ID — just set the value in .env.")
print(f"\nPRINTIFY_SHOP_ID = {shop_id}")
