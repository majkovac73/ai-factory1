"""
One-off — replace listing 4534427807's octet-stream file with a correctly
typed image/png file so it displays in Etsy's editor.

Context (step 94): the file design.png was attached but stored with
filetype=application/octet-stream (upload_digital_file() hardcoded that
content-type), so Etsy's editor never displayed it. Cache ruled out (Maj
hard-refreshed, still missing); taxonomy fix ruled out (file still present
after). The fix is to re-upload the same bytes with content-type image/png.

Etsy files are immutable (no in-place type change), so this UPLOADS the
corrected file first (count -> 2), verifies it, then DELETES the old
octet-stream file (count -> 1). Order matters: deleting the FINAL file of a
digital listing converts it to physical, so the count must never pass
through zero.

Self-contained raw httpx (the deployed container doesn't have the new
EtsyImageService methods yet). Same print-then-confirm safety pattern as
prior production writes this session.

Run via railway ssh (chunked base64 transfer — see MIGRATION_NOTES.md):
  railway ssh -- "... python3 scripts/fix_filetype_4534427807.py"        # dry run
  railway ssh -- "... python3 scripts/fix_filetype_4534427807.py --yes"  # apply
"""
import asyncio
import mimetypes
import sys
import os
import httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
LISTING_ID = "4534427807"
FILE_PATH = "/data/images/delivery/fb66a81a-b7be-48db-843c-5aed7a87383e/design.png"
BAD_TYPE = "application/octet-stream"


async def get_files(client, headers):
    r = await client.get(
        f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{LISTING_ID}/files",
        headers=headers,
    )
    r.raise_for_status()
    return r.json().get("results", [])


async def main():
    access_token = await get_valid_access_token()
    api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
    headers = {"Authorization": f"Bearer {access_token}", "x-api-key": api_key_header}

    filename = os.path.basename(FILE_PATH)
    content_type = mimetypes.guess_type(FILE_PATH)[0] or BAD_TYPE

    async with httpx.AsyncClient(timeout=60.0) as client:
        files_before = await get_files(client, headers)
        print(f"Listing {LISTING_ID} — current files:")
        for f in files_before:
            print(f"  id={f['listing_file_id']} name={f['filename']} filetype={f['filetype']}")

        bad_files = [f for f in files_before if f.get("filetype") == BAD_TYPE]
        if not bad_files:
            print("\nNo octet-stream file present. Nothing to do.")
            return

        print(f"\nWill: upload {filename!r} as {content_type} (count -> {len(files_before)+1}),")
        print(f"      then delete {len(bad_files)} octet-stream file(s) (count -> {len(files_before)+1-len(bad_files)}).")
        print(f"      (upload first, delete second — count never passes through zero)")

        if "--yes" not in sys.argv:
            print("\nDry run only. Re-run with --yes to apply.")
            return

        # 1. Upload corrected file FIRST
        with open(FILE_PATH, "rb") as fh:
            file_bytes = fh.read()
        up = await client.post(
            f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{LISTING_ID}/files",
            headers=headers,
            files={"file": (filename, file_bytes, content_type)},
            data={"name": filename, "rank": 1},
        )
        up.raise_for_status()
        new_file = up.json()
        new_id = new_file.get("listing_file_id")
        print(f"\nUploaded new file: id={new_id} filetype={new_file.get('filetype')}")

        # 2. Verify the new file really has the right type (readback)
        mid = await get_files(client, headers)
        new_row = next((f for f in mid if f["listing_file_id"] == new_id), None)
        if not new_row or new_row.get("filetype") == BAD_TYPE:
            print(f"ABORT: new file readback shows {new_row.get('filetype') if new_row else 'missing'} — NOT deleting old file.")
            return
        print(f"Readback confirms new file filetype={new_row['filetype']}")

        # 3. Delete the old octet-stream file(s) SECOND
        for f in bad_files:
            d = await client.delete(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{LISTING_ID}/files/{f['listing_file_id']}",
                headers=headers,
            )
            d.raise_for_status()
            print(f"Deleted old octet-stream file id={f['listing_file_id']}")

        # 4. Final state
        files_after = await get_files(client, headers)
        print("\nFinal files:")
        for f in files_after:
            print(f"  id={f['listing_file_id']} name={f['filename']} filetype={f['filetype']}")
        good = [f for f in files_after if f.get("filetype") != BAD_TYPE]
        if files_after and not any(f.get("filetype") == BAD_TYPE for f in files_after):
            print("\nCONFIRMED: listing now has only correctly-typed file(s).")
        else:
            print("\nWARNING: final state still has an octet-stream file or no files — review manually.")


asyncio.run(main())
