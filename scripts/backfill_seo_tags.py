"""
A-4 backfill — bring existing ACTIVE Etsy listings up to a full 13 tags.

DRY-RUN by default: prints before/after tags for every listing with <13 tags so
Maj can approve. With --apply it PATCHes tags only (a cheap text update; nothing
else about the listing changes) via EtsyClient.update_listing.

Tags are regenerated deterministically from the listing's existing tags + title
(same ListingGeneratorAgent._derive_tags used for new listings) — no LLM call,
no cost.

Usage (via railway ssh in prod, where the Etsy token lives):
  python scripts/backfill_seo_tags.py            # dry run
  python scripts/backfill_seo_tags.py --apply     # PATCH tags
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.agents.etsy.listing_generator import ListingGeneratorAgent
from app.services.etsy_client import EtsyClient
from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


async def fetch_active_listings() -> list:
    at = await get_valid_access_token()
    headers = {"Authorization": f"Bearer {at}",
               "x-api-key": f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"}
    listings, offset = [], 0
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            r = await client.get(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/active",
                headers=headers, params={"limit": 100, "offset": offset},
            )
            r.raise_for_status()
            page = r.json().get("results", [])
            listings.extend(page)
            if len(page) < 100:
                break
            offset += 100
    return listings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    gen = ListingGeneratorAgent()
    listings = asyncio.run(fetch_active_listings())
    print(f"Fetched {len(listings)} active listings.\n")

    to_fix = []
    for listing in listings:
        tags = listing.get("tags", []) or []
        if len(tags) >= 13:
            continue
        title = listing.get("title", "")
        new_tags = gen._derive_tags(list(tags), product_name=title)
        if len(new_tags) > len(tags):
            to_fix.append((listing, tags, new_tags))
            print(f"[{listing.get('listing_id')}] {title[:60]}")
            print(f"   before ({len(tags)}): {tags}")
            print(f"   after  ({len(new_tags)}): {new_tags}\n")

    print(f"{len(to_fix)} listings would get more tags.")
    if not args.apply:
        print("Dry run — re-run with --apply to PATCH tags.")
        return

    for listing, _old, new_tags in to_fix:
        lid = listing.get("listing_id")
        try:
            asyncio.run(EtsyClient().update_listing(str(lid), {"tags": new_tags[:13]}))
            print(f"Updated {lid} -> {len(new_tags[:13])} tags")
        except Exception as e:
            print(f"FAILED {lid}: {e}")


if __name__ == "__main__":
    main()
