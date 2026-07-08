
## Step 94 — Digital file attached but invisible in Etsy's editor: octet-stream content-type
**Date:** 2026-07-08

---

### Investigation (all from live, real API calls)

Symptom: listing 4534427807's digital file was attached per the API, but
never appeared in Etsy's listing editor — even after the step-93 taxonomy
fix, and even for Maj compared side-by-side against a manually-made
listing whose file displays.

1. **File still attached right now?** Yes. `getAllListingFiles` returns
   `count: 1`, same `listing_file_id: 1499536729759`, same
   `create_timestamp` as the original upload; the listing object shows
   `file_data: "1 File"`. The file is genuinely present.
2. **Did the taxonomy `update_listing()` PATCH destroy/clear the file? —
   DISPROVEN.** File ID and timestamp unchanged after the taxonomy PATCH.
   Etsy's `updateListing` is a genuine partial update (only touched
   `taxonomy_id`), so there was never a mechanism to clear files.
   Explanation 2 ruled out.
3. **Field-by-field vs. the known-good reference (Maj's listing
   1900562269, confirmed by him).** The decisive difference is the file's
   stored `filetype`:

   | | 4534427807 (broken) | 1900562269 (displays) |
   |---|---|---|
   | **filetype** | **`application/octet-stream`** | **`application/pdf`** |
   | when_made | made_to_order | 2007_2009 |
   | should_auto_renew | False | True |

   Our `EtsyImageService.upload_digital_file()` **hardcoded
   `application/octet-stream`** as the multipart content-type for every
   upload. Etsy stores exactly the content-type it's sent, and its editor
   only renders files with a recognised type — so the file was stored as
   an unrecognised generic binary and never displayed, even though the
   bytes are attached and counted.
4. **Cheap explanation (browser cache) — ruled out by Maj.** He
   hard-refreshed the editor; the file was still missing. So it's a real
   data-level cause, not a stale view.

**Root cause: the hardcoded `application/octet-stream` content-type in
`upload_digital_file()`.** Same class of bug as everything else this
session — technically "succeeds" (201 Created, file present) but
functionally wrong, uncaught by any check (step 92's readback verified a
file was *present*, count >= 1, but not that it was of a *usable/displayable
type*).

---

### Fix

**`app/services/etsy_image_service.py`:**
- `upload_digital_file()` now derives the real MIME type from the file
  extension via `mimetypes.guess_type()` (`.png` → `image/png`, `.pdf` →
  `application/pdf`), falling back to `application/octet-stream` only when
  the extension is genuinely unknown. New `_guess_content_type()` helper +
  `GENERIC_BINARY_CONTENT_TYPE` constant.
- New `delete_listing_file()` method (`DELETE
  /shops/{shop_id}/listings/{listing_id}/files/{listing_file_id}`,
  shop-scoped per Etsy's real spec) — with the documented caution that
  deleting the *final* file converts a digital listing to physical, so
  replacements must upload-first-then-delete.

**`app/services/pipeline_orchestrator.py`** (`_stage_attach_publish`,
extending the step-92 file readback): after confirming ≥1 file is
attached, it now also requires at least one attached file to have a
**recognised (non-octet-stream) `filetype`**. An octet-stream-only file
set is treated as a readback failure — delete the listing, mark
`BLOCKED_NO_PRODUCT` — exactly like every other readback failure. This
specific check is what step 92's "count ≥ 1" check missed.

---

### Live listing fix (4534427807)

Etsy files are immutable (no in-place type change), so `design.png` was
**re-uploaded as `image/png` first** (file count → 2), the new file's type
was confirmed via readback, and only **then** was the old octet-stream file
deleted (count → 1) — never passing through zero (which would have
converted the listing to physical). Final confirmed state: a single file,
`design.png`, `filetype: image/png` (new `listing_file_id 1499561613281`).
Task `fb66a81a` annotated. **Whether this makes the file display in the
editor is the final confirmation of the hypothesis — pending Maj's check.**

---

### Tests

`scripts/test_step94_file_content_type.py` (new, 4/4 pass):
1. `_guess_content_type()`: `.png`→`image/png`, `.pdf`→`application/pdf`,
   unknown→octet-stream fallback.
2. `upload_digital_file()` sends the real MIME type in the multipart
   content-type (captured via a fake httpx client).
3. Orchestrator: a file present but stored as octet-stream (count ≥ 1 but
   undisplayable) → listing deleted, task blocked (would have caught the
   real bug).
4. Orchestrator: a file with a real MIME type passes the gate.

All 19 suites (68–94) pass. test_step89's tests [4]/[5]/[7] were refactored
off a crude `patch("asyncio.run", return_value=fixed_dict)` shortcut (which
collided with the multiple differently-shaped readback calls now in the
flow) onto proper async fakes.

---

### Files touched
- `app/services/etsy_image_service.py` — real MIME type on upload, `delete_listing_file()`.
- `app/services/pipeline_orchestrator.py` — octet-stream filetype readback gate.
- `scripts/test_step89..94_*.py` — doubles updated with `filetype`; test_step94 new.
- `scripts/fix_filetype_4534427807.py` — one-off live fix (already run).
