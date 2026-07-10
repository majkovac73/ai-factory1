"""
Audit existing published Etsy listings against ProductViabilityCriticAgent.

This is a DRY-RUN reporting tool by default — it does NOT deactivate or
delete anything on its own. It prints/saves a report so Maj can review
and decide what to do with each flagged listing. Use --deactivate only
after reviewing the report, and even then it asks for explicit
confirmation per listing (see below) — never bulk-deletes automatically.

Usage:
  python scripts/audit_existing_listings.py                  # report only
  python scripts/audit_existing_listings.py --deactivate      # also offers
                                                              # to deactivate
                                                              # flagged listings,
                                                              # one at a time,
                                                              # with confirmation

Run in production via railway ssh (the Etsy token lives in the container DB):
  railway ssh -- "... python3 scripts/audit_existing_listings.py"
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.agents.product_viability_critic import ProductViabilityCriticAgent
from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


async def _headers():
    at = await get_valid_access_token()
    return {
        "Authorization": f"Bearer {at}",
        "x-api-key": f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}",
    }


async def fetch_active_listings() -> list:
    """
    Page through every ACTIVE listing in the shop.
    Etsy endpoint: GET /shops/{shop_id}/listings/active (limit<=100, offset).
    Returns the raw listing dicts.
    """
    headers = await _headers()
    listings = []
    offset = 0
    limit = 100
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            r = await client.get(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/active",
                headers=headers,
                params={"limit": limit, "offset": offset, "includes": "Images"},
            )
            if r.status_code >= 400:
                raise Exception(f"Etsy API error {r.status_code}: {r.text}")
            results = r.json().get("results", [])
            listings.extend(results)
            if len(results) < limit:
                break
            offset += limit
    return listings


async def deactivate_listing(listing_id) -> None:
    """PATCH the listing state to 'inactive' (Etsy has no hard 'deactivate' verb)."""
    headers = await _headers()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.patch(
            f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{listing_id}",
            headers=headers,
            json={"state": "inactive"},
        )
        if r.status_code >= 400:
            raise Exception(f"Etsy API error {r.status_code}: {r.text}")


def _listing_url(listing):
    return listing.get("url") or f"https://www.etsy.com/listing/{listing.get('listing_id')}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deactivate", action="store_true")
    args = parser.parse_args()

    critic = ProductViabilityCriticAgent()

    listings = asyncio.run(fetch_active_listings())
    print(f"Fetched {len(listings)} active listings from shop {settings.ETSY_SHOP_ID}.\n")

    results = []
    for listing in listings:
        concept = {
            "product_name": listing.get("title"),
            "description": (listing.get("description") or "")[:600],
            # Best available proxy for format — Etsy exposes taxonomy_id, not our
            # internal product_format. Noted in the report so the reader knows the
            # critic judged with a coarser format signal than the live pipeline.
            "product_format": listing.get("taxonomy_id"),
            "target_audience": "",
            "buyer_reason": "",
        }
        critique = critic.critique(concept)
        results.append({
            "listing_id": listing.get("listing_id"),
            "title": listing.get("title"),
            "url": _listing_url(listing),
            "passed": critique["passed"],
            "score": critique["score"],
            "reason": critique["reason"],
            "note": "format judged from taxonomy_id proxy, not internal product_format",
        })
        print(
            f"[{'PASS' if critique['passed'] else 'FLAG'}] "
            f"score={critique['score']} '{listing.get('title')}' — {critique['reason']}"
        )

    flagged = [r for r in results if not r["passed"]]
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "audit_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{len(flagged)} of {len(results)} listings flagged. Full report: {report_path}")

    if args.deactivate and flagged:
        for r in flagged:
            answer = input(
                f"\nDeactivate listing {r['listing_id']} \"{r['title']}\" "
                f"(score={r['score']}, reason: {r['reason']})? [y/N] "
            )
            if answer.strip().lower() == "y":
                asyncio.run(deactivate_listing(r["listing_id"]))
                print(f"Deactivated {r['listing_id']}")
            else:
                print("Skipped.")


if __name__ == "__main__":
    main()
