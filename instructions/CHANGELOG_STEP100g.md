# Step 100g — Digital listing photos derived from the real delivery design (the actual root-cause fix)

**Date:** 2026-07-09

## Why (live test finding)

After deploying 100c–100f, I triggered a **real** `coloring_page` task
(`e881c422`) in production to verify. It **still BLOCKED** at the consistency
gate:

> BLOCKED_NO_PRODUCT — marketing/deliverable mismatch: marketing image 2: shows a
> different illustrated dinosaur design; lacks color and has a different body shape

**Root cause (confirmed):** the hero/lifestyle marketing images are **independent
text-to-image generations** — a separate Seedream call from the delivery asset.
Two independent generations of "dinosaur coloring page" produce genuinely
*different dinosaurs*. The consistency gate (now correctly reasoning at the
subject level, per 100e) rightly flags "a different illustrated dinosaur". The
prompt-based fixes (100d honest line-art framing, 100e recalibration, 100f pdf
framing) **cannot** solve this — 100d made both images line-art, but they're
still *different drawings*. No prompt or remake can make two independent
generations depict the same specific illustration.

## The fix (chosen with Maj: "mockups from delivery")

For **digital single-image** formats (`digital_required and not is_pdf`:
single_print, coloring_page, greeting_card_design, phone_wallpaper,
sticker_sheet_design), the listing photos are now **derived from the actual
delivery design** instead of independently generated:

- The content-verified **delivery file itself** is the featured/primary photo
  (as before, prepended).
- Two additional listing photos are **PIL mockups of that same delivery design**
  (the real design centered on clean neutral/warm backgrounds) — a standard,
  professional way to present a digital print.

Because every listing photo now *is* the delivered design, the consistency gate
passes **truthfully** on the first check — no remake loop, no block. This also
**removes 2 Seedream calls per digital product** (the independent hero/lifestyle
generations), a direct cost saving.

POD and PDF formats are **unchanged** — they keep independent generation (their
listing photos legitimately differ from the flat delivery asset). *(Note: PDF
almost certainly needs an analogous delivery-derived treatment — its independent
"flatlay" is the same class of problem — but that's a separate follow-up; it was
not live-tested and costs 5× the images. Flagged, not done here.)*

### Code

- `pipeline_orchestrator._build_listing_mockups(task_id, delivery_path, report)` —
  new PIL helper: composites the delivery design onto two 1024×1024 backgrounds,
  validates (ImageValidationService, use_case="listing") and registers each as a
  `listing` catalog asset (agent `DeliveryMockup`). Returns [] gracefully on
  failure (the prepended delivery still stands as the listing photo).
- `run_post_completion`: for digital single-image formats, step 1 no longer calls
  `_stage_listing_images` (skips independent generation); step 2.6 builds the
  mockups from the verified delivery and sets `image_paths = [delivery] + mockups`.
- The consistency check is still run (not weakened/skipped) — it now simply
  passes because the images honestly depict the delivered design.

The independent-generation path (`_stage_listing_images` / `ProductImageAgent`)
and the remake logic remain intact for POD/PDF.

## Tests

- **`scripts/test_step100g_delivery_mockups.py`** (4/4): `_build_listing_mockups`
  produces 2 valid 1024×1024 listing PNGs; the mockups actually **contain the
  delivered design** (centre pixels match the delivery, corners are background);
  they're registered as `listing` by `DeliveryMockup`; and an unreadable delivery
  is handled gracefully (empty result, failure recorded, no crash).
- **`test_step89`** [2] and [7] updated to the new behavior: [2] asserts a digital
  single-image format derives listing photos from delivery mockups (source
  `delivery_mockup`) with ProductImageAgent **not** called; [7] asserts a mockup
  failure is isolated (the delivery design still stands as the photo, downstream
  stages still run).
- Full regression green: steps **69, 89–96, 98, 100b, 100d, 100f, 100g**.

## Verify

Deployed, then re-ran a real `coloring_page` task to confirm it now passes the
consistency gate on the first check (0 remakes) and creates a listing — the exact
scenario that blocked as `e881c422`. (Result recorded in the deploy/verify notes.)
