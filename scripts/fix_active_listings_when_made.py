"""
One-off — fix the two live ACTIVE digital listings whose files are hidden.

Both are digital downloads created before the step-95 fix, so both have
when_made="made_to_order" (Etsy hides the instant-download file slot). One
also still has an octet-stream file (step-94 issue). This makes each fully
displayable: (a) if the file is octet-stream, swap it for image/png
(upload-first-then-delete so count never hits zero), then (b) set
when_made=2020_2026 (with who_made + is_supply, which Etsy requires
together). Readback-confirms each change.

Scope: only the two ACTIVE, real-product listings. The two BLOCKED_NO_PRODUCT
draft listings (4534356981, 4534362096) are deliberately NOT touched —
they're invalid concepts that shouldn't be listings at all and warrant
deletion, not a category/when_made fix.

Same print-then-confirm safety pattern as prior production writes.

Run via railway ssh (chunked base64 — see MIGRATION_NOTES.md):
  railway ssh -- "... python3 scripts/fix_active_listings_when_made.py"        # dry run
  railway ssh -- "... python3 scripts/fix_active_listings_when_made.py --yes"  # apply
"""
import asyncio
import mimetypes
import os
import sys
import httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
BAD_TYPE = "application/octet-stream"
NEW_WHEN_MADE = "2020_2026"

# listing_id -> source design file on the container disk (for the octet-stream swap)
LISTINGS = {
    "4534511735": "/data/images/delivery/623824bb-7a0b-464b-ae81-31012990bc90/design.png",
    "4534525046": "/data/images/delivery/f4b7ecc4-3a51-4937-af28-2c4da35c1191/design.png",
}


async def get_files(client, headers, lid):
    r = await client.get(f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{lid}/files", headers=headers)
    r.raise_for_status()
    return r.json().get("results", [])


async def get_listing(client, headers, lid):
    r = await client.get(f"{ETSY_API_BASE}/listings/{lid}", headers=headers)
    r.raise_for_status()
    return r.json()


async def fix_one(client, headers, lid, src, apply):
    listing = await get_listing(client, headers, lid)
    files = await get_files(client, headers, lid)
    print(f"\n=== {lid} — {listing.get('title')!r} ===")
    print(f"  when_made={listing.get('when_made')}  files={[(f['filename'], f['filetype']) for f in files]}")

    bad_files = [f for f in files if f.get("filetype") == BAD_TYPE]
    needs_filetype = bool(bad_files)
    needs_when_made = listing.get("when_made") == "made_to_order"

    if not needs_filetype and not needs_when_made:
        print("  Already fully fixed. Nothing to do.")
        return
    print(f"  Plan: {'swap octet-stream->image/png; ' if needs_filetype else ''}"
          f"{'set when_made=' + NEW_WHEN_MADE if needs_when_made else ''}")

    if not apply:
        return

    # (a) filetype swap — upload correct type FIRST, verify, then delete old
    if needs_filetype:
        content_type = mimetypes.guess_type(src)[0] or BAD_TYPE
        with open(src, "rb") as fh:
            data = fh.read()
        up = await client.post(
            f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{lid}/files",
            headers=headers,
            files={"file": (os.path.basename(src), data, content_type)},
            data={"name": os.path.basename(src), "rank": 1})
        up.raise_for_status()
        new_id = up.json().get("listing_file_id")
        mid = await get_files(client, headers, lid)
        new_row = next((f for f in mid if f["listing_file_id"] == new_id), None)
        if not new_row or new_row.get("filetype") == BAD_TYPE:
            print("  ABORT filetype swap: new file readback not a recognised type; NOT deleting old.")
            return
        print(f"  uploaded new file id={new_id} filetype={new_row['filetype']}")
        for f in bad_files:
            d = await client.delete(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{lid}/files/{f['listing_file_id']}",
                headers=headers)
            d.raise_for_status()
            print(f"  deleted old octet-stream file id={f['listing_file_id']}")

    # (b) when_made fix (Etsy requires when_made + who_made + is_supply together)
    if needs_when_made:
        p = await client.patch(
            f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{lid}",
            headers=headers,
            json={"when_made": NEW_WHEN_MADE, "who_made": "i_did", "is_supply": False})
        p.raise_for_status()
        print(f"  set when_made -> {p.json().get('when_made')}")

    # readback confirm
    listing2 = await get_listing(client, headers, lid)
    files2 = await get_files(client, headers, lid)
    wm_ok = listing2.get("when_made") != "made_to_order"
    ft_ok = files2 and all(f.get("filetype") != BAD_TYPE for f in files2)
    print(f"  READBACK: when_made={listing2.get('when_made')} ({'ok' if wm_ok else 'STILL BAD'}), "
          f"files={[(f['filename'], f['filetype']) for f in files2]} ({'ok' if ft_ok else 'STILL BAD'})")


async def main():
    apply = "--yes" in sys.argv
    at = await get_valid_access_token()
    headers = {"Authorization": f"Bearer {at}",
               "x-api-key": f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"}
    async with httpx.AsyncClient(timeout=60) as client:
        for lid, src in LISTINGS.items():
            await fix_one(client, headers, lid, src, apply)
    if not apply:
        print("\nDry run only. Re-run with --yes to apply.")


asyncio.run(main())
