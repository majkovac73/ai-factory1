"""
B-7 setup — create Etsy shop sections and print the SHOP_SECTION_MAP env value.

Buyers who click through see an anonymous default shop; sections make it look
intentional (trust = conversion, especially with zero reviews). This creates one
section per product group and prints a JSON mapping to paste into the
SHOP_SECTION_MAP Railway env, after which new listings land in the right section.

Idempotent-ish: skips creating a section whose title already exists.

Run via railway ssh (Etsy token lives in the container):
  python scripts/create_shop_sections.py            # dry run (lists what it would create)
  python scripts/create_shop_sections.py --apply     # create + print the env map
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"

# product_format -> section title
FORMAT_SECTIONS = {
    "single_print": "Wall Art",
    "pdf_planner_or_guide": "Planners & Guides",
    "coloring_page": "Coloring Pages",
    "phone_wallpaper": "Phone Wallpapers",
    "greeting_card_design": "Cards & Stickers",
    "sticker_sheet_design": "Cards & Stickers",
    "pod_apparel_design": "Apparel",
}


async def _headers():
    at = await get_valid_access_token()
    return {"Authorization": f"Bearer {at}", "x-api-key": f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"}


async def existing_sections(client, headers):
    r = await client.get(f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/sections", headers=headers)
    r.raise_for_status()
    return {s["title"]: s["shop_section_id"] for s in r.json().get("results", [])}


async def run(apply: bool):
    headers = await _headers()
    titles = sorted(set(FORMAT_SECTIONS.values()))
    async with httpx.AsyncClient(timeout=60) as client:
        have = await existing_sections(client, headers)
        title_to_id = dict(have)
        for title in titles:
            if title in have:
                print(f"exists: {title} -> {have[title]}")
                continue
            if not apply:
                print(f"would create: {title}")
                continue
            r = await client.post(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/sections",
                headers=headers, data={"title": title},
            )
            r.raise_for_status()
            sid = r.json().get("shop_section_id")
            title_to_id[title] = sid
            print(f"created: {title} -> {sid}")

    fmt_map = {fmt: title_to_id[t] for fmt, t in FORMAT_SECTIONS.items() if t in title_to_id}
    print("\nSet this in Railway as SHOP_SECTION_MAP:")
    print(json.dumps(fmt_map))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.apply))


if __name__ == "__main__":
    main()
