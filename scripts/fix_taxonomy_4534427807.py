"""
One-off — correct the taxonomy_id on live listing 4534427807.

Context (step 93): every listing this system has ever created was silently
assigned taxonomy_id=1 ("Accessories", Etsy's top-level, most-generic node)
because nothing upstream of EtsyClient.create_draft_listing() ever set
taxonomy_id at all. Confirmed live against Etsy's own seller-taxonomy tree
and its "too broad" warning in the listing editor. This listing
(fb66a81a / "Customizable Family Recipe Greeting Card") is a
greeting_card_design product -- the correct specific leaf per the real
taxonomy tree is 1280 ("Just Because Cards", under Paper & Party Supplies
> Greeting Cards > Just Because Cards).

Self-contained (raw httpx calls) rather than depending on EtsyClient's new
get_listing/update_listing methods, since those aren't deployed yet --
matches the pattern used for this session's live investigation calls.

Same safety pattern as prior production writes this session: print the
real current state (fetched live), require an explicit --yes to apply,
call Etsy's real updateListing endpoint, then independently re-fetch to
confirm the change actually took effect.

Run via railway ssh (chunked base64 transfer -- see MIGRATION_NOTES.md):
  railway ssh -- "... python3 scripts/fix_taxonomy_4534427807.py"          # dry run
  railway ssh -- "... python3 scripts/fix_taxonomy_4534427807.py --yes"   # apply
"""
import asyncio
import sys
import os
import httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
LISTING_ID = "4534427807"
NEW_TAXONOMY_ID = 1280  # "Just Because Cards"


async def get_listing(client, headers):
    resp = await client.get(f"{ETSY_API_BASE}/listings/{LISTING_ID}", headers=headers)
    resp.raise_for_status()
    return resp.json()


async def main():
    access_token = await get_valid_access_token()
    api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
    headers = {"Authorization": f"Bearer {access_token}", "x-api-key": api_key_header}

    async with httpx.AsyncClient() as client:
        current = await get_listing(client, headers)
        current_taxonomy = current.get("taxonomy_id")
        print(f"Listing {LISTING_ID} ({current.get('title')!r})")
        print(f"  current taxonomy_id: {current_taxonomy}")
        print(f"  new taxonomy_id:     {NEW_TAXONOMY_ID} (Just Because Cards)")

        if current_taxonomy == NEW_TAXONOMY_ID:
            print("\nAlready correct. Nothing to do.")
            return

        if "--yes" not in sys.argv:
            print("\nDry run only. Re-run with --yes to apply this change.")
            return

        resp = await client.patch(
            f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{LISTING_ID}",
            headers=headers,
            json={"taxonomy_id": NEW_TAXONOMY_ID},
        )
        resp.raise_for_status()
        updated = resp.json()
        print(f"\nUpdate response taxonomy_id: {updated.get('taxonomy_id')}")

        # Independent readback — don't trust the update response alone.
        readback = await get_listing(client, headers)
        readback_taxonomy = readback.get("taxonomy_id")
        print(f"Independent readback taxonomy_id: {readback_taxonomy}")

        if readback_taxonomy == NEW_TAXONOMY_ID:
            print("\nCONFIRMED: taxonomy_id change took effect.")
        else:
            print(f"\nWARNING: readback shows {readback_taxonomy}, expected {NEW_TAXONOMY_ID} — change may not have taken effect.")


asyncio.run(main())
