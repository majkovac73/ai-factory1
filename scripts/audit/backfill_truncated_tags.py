"""
Audit 2026-07-20 #7 backfill — fix truncated / under-filled tags on LIVE listings.

The mid-word 20-char truncation ("classroom organizati") and under-filled tag
slots (18/45 listings used only 3-8 of 13) waste the top of the funnel. The
generator is fixed going forward; this repairs the EXISTING catalog in place.

For each active listing it:
  - normalizes every tag to a whole-word <= 20-char tag (ListingGenerator
    ._to_valid_tag), dropping any that can't be salvaged;
  - pads back to 13 valid tags from the listing's own title n-grams + the generic
    filler pool;
  - PATCHes the listing only if the tag set actually changed.

Dry-run by default; pass --apply to write. Run INSIDE the Railway container
(has Etsy OAuth + shop id). Read-safe; --apply mutates listings.

Usage:
  python scripts/audit/backfill_truncated_tags.py            # dry-run
  python scripts/audit/backfill_truncated_tags.py --apply
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import settings
from app.agents.etsy.listing_generator import ListingGeneratorAgent


def _needs_fix(tags: list) -> bool:
    if len(tags or []) < ListingGeneratorAgent.MAX_TAGS:
        return True
    for t in tags or []:
        if not isinstance(t, str) or len(t) > ListingGeneratorAgent.MAX_TAG_LENGTH or t != t.strip():
            return True
    return False


def _rebuild_tags(title: str, tags: list) -> list:
    gen = ListingGeneratorAgent.__new__(ListingGeneratorAgent)  # no LLM/provider needed
    # Seed from existing (normalized) tags as "keywords", pad from title n-grams.
    ngrams = ListingGeneratorAgent.title_ngrams([title], max_terms=13)
    return gen._derive_tags(keywords=list(tags or []), product_name=title, extra_terms=ngrams)


async def run(apply: bool):
    import httpx
    from app.services import etsy_oauth
    from app.services.etsy_client import EtsyClient

    tok = await etsy_oauth.get_valid_access_token()
    shop = settings.ETSY_SHOP_ID
    key = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
    H = {"x-api-key": key, "Authorization": f"Bearer {tok}"}

    fixed = scanned = 0
    async with httpx.AsyncClient(timeout=30) as cl:
        offset = 0
        while True:
            r = await cl.get(
                f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/active",
                headers=H, params={"limit": 100, "offset": offset},
            )
            r.raise_for_status()
            results = r.json().get("results", []) or []
            if not results:
                break
            for L in results:
                scanned += 1
                lid = L.get("listing_id")
                tags = L.get("tags", []) or []
                if not _needs_fix(tags):
                    continue
                new_tags = _rebuild_tags(L.get("title", ""), tags)
                if new_tags == tags:
                    continue
                fixed += 1
                print(f"listing {lid}: {len(tags)} -> {len(new_tags)} tags")
                print(f"  old: {tags}")
                print(f"  new: {new_tags}")
                if apply:
                    await EtsyClient().update_listing(str(lid), {"tags": new_tags})
                    await asyncio.sleep(1.0)  # gentle: avoid Etsy 429
            offset += 100

    print(f"\nScanned {scanned} active listings; {'fixed' if apply else 'would fix'} {fixed}.")


if __name__ == "__main__":
    asyncio.run(run(apply="--apply" in sys.argv))
