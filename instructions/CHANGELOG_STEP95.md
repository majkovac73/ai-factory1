
## Step 95 — Digital file invisible in editor: when_made=made_to_order (the ACTUAL root cause)
**Date:** 2026-07-08

---

### Context — two prior wrong hypotheses

The "digital file attached but not visible in Etsy's editor" symptom on
listing 4534427807 was mis-diagnosed twice before this:
- Step 93 blamed the broad `taxonomy_id=1` — fixing it did not restore the
  file display.
- Step 94 blamed the `application/octet-stream` filetype — fixing that
  (re-uploading as `image/png`) also did not restore the display; Maj
  confirmed the file was still missing after a hard refresh.

Both were real, worth-fixing bugs — but neither was THE cause of this
symptom.

### Root cause (confirmed by a live single-variable experiment)

A complete field-by-field diff of the broken listing against Maj's
known-good manual listing (1900562269, confirmed by him) left one relevant
difference: **`when_made`** — `"made_to_order"` (broken) vs `"2007_2009"`
(working). This matches exactly what Maj saw in the editor: *"Digital files
• **Made to order**"*.

Hypothesis: Etsy treats a `made_to_order` digital listing as a
personalized/custom item the seller delivers manually after purchase, so
its editor hides the instant-download file slot even though a file is
attached via API. Our `create_draft_listing()` hardcoded
`when_made: "made_to_order"` for every listing.

**Verified before touching any code** (having been wrong twice): changed
ONLY `when_made` on the live listing 4534427807 from `made_to_order` to
`2020_2026` (Etsy requires `when_made` + `who_made` + `is_supply` to be
sent together — a 400 otherwise), left everything else identical, and Maj
confirmed the download file now displays in the editor. Root cause
confirmed. `made_to_order` IS correct for POD physical goods (printed
after purchase) — this only applies to instant digital downloads.

---

### Fix

**`app/services/etsy_client.py`:**
- `create_draft_listing()` no longer hardcodes `when_made` — it uses
  `listing.get("when_made", POD_WHEN_MADE)`.
- New constants: `DIGITAL_WHEN_MADE = "2020_2026"` (a real recent-era value
  from Etsy's live `when_made` enum) and `POD_WHEN_MADE = "made_to_order"`.
  Note in code: if Etsy rolls the enum past 2026, `DIGITAL_WHEN_MADE` needs
  bumping — the new readback (below) will surface it loudly if it's ever
  rejected/wrong.
- New `update_listing()` was already added in step 93; `when_made` updates
  must include `who_made` + `is_supply` (Etsy rejects a lone `when_made`).

**`app/services/pipeline_orchestrator.py`** (`_stage_create_listing`):
- Sets `when_made` per type: `DIGITAL_WHEN_MADE` for digital downloads,
  `POD_WHEN_MADE` for POD physical.
- The existing create-time readback (step 93) now verifies `when_made` in
  addition to `taxonomy_id`: if the real listing's `when_made` doesn't
  match what was intended (Etsy silently keeping made_to_order, or a
  regression), the listing is deleted and the task blocked — same pattern
  as every other readback failure. This specific check would have caught
  the original bug.

---

### Live listings fixed

Audited **every** listing in the shop via Etsy's own
`GET /shops/{shop_id}/listings` (all states) for the
`type=download AND when_made=made_to_order` combination:

| listing_id | title | action |
|---|---|---|
| 4534427807 | Customizable Family Recipe Greeting Card | when_made→2020_2026 (during the diagnosis; Maj confirmed file now displays) |
| 4534511735 | Customizable Family Tree Coloring Page (active) | **Fixed: when_made→2020_2026** (file was already image/png). Readback confirmed. |
| 4534525046 | Mindful Moments Daily Affirmation Coloring Page (active) | **Fixed: octet-stream file swapped→image/png AND when_made→2020_2026.** Readback confirmed both. |
| 4534356981 | AR Home Decor Visualizer (draft, BLOCKED_NO_PRODUCT) | **Not touched** — invalid concept, should be deleted, not fixed |
| 4534362096 | Eco-Friendly Digital Downloads (draft, BLOCKED_NO_PRODUCT) | **Not touched** — same |

All fixes used the upload-first-then-delete order for filetype swaps (never
letting the file count hit zero, which would convert a digital listing to
physical), and each change was independently readback-confirmed.

**Still outstanding (flagged, out of scope):** the two BLOCKED_NO_PRODUCT
draft listings (4534356981, 4534362096) are invalid products still sitting
on Etsy as drafts — their `_cleanup_unbacked_listing()` delete apparently
failed silently when they were first blocked. They warrant deletion (a
separate bug: why did the cleanup delete fail?), not a when_made fix.

---

### Tests

`scripts/test_step95_when_made.py` (new, 3/3 pass):
1. `create_draft_listing()` uses the caller's `when_made`, defaulting to
   `made_to_order` only when unspecified.
2. Orchestrator sends a non-`made_to_order` `when_made` for digital and
   `made_to_order` for POD.
3. Create-time readback: a digital listing whose real `when_made` is
   `made_to_order` → listing deleted, task blocked (would have caught the
   real bug).

All 20 suites (68–95) pass. Existing `get_listing` test doubles were
updated to echo back `when_made` alongside `taxonomy_id`.

---

### Files touched
- `app/services/etsy_client.py` — `when_made` no longer hardcoded; DIGITAL/POD constants.
- `app/services/pipeline_orchestrator.py` — per-type `when_made` + readback.
- `scripts/test_step89..95_*.py` — doubles echo `when_made`; test_step95 new.
- `scripts/fix_active_listings_when_made.py` — one-off live fix (already run).
