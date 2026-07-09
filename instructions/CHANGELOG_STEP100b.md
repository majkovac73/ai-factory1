# Step 100b — Consistency failures trigger a targeted, feedback-driven remake (not an immediate block)

**Date:** 2026-07-09

## Problem

Real prod task **194a1933** was `BLOCKED_NO_PRODUCT`: the marketing/deliverable
consistency check found marketing images 3 and 4 depicting *different* designs
(a floral border, an alternate mandala) than the actual delivery asset. The
delivery asset itself **passed** content-quality QA — it was fine. Only the
independently-generated marketing/listing photos were wrong, yet a mismatch
blocked the whole task outright. Blocking a real, correct product because a
preview photo drifted is too blunt.

## Fix — regenerate only the wrong image(s), with the vision model's own feedback

### Step 1 — per-image mismatch schema (`content_quality_service.py`)

`check_marketing_consistency()` now returns *which* marketing image mismatched
and *why*, not just an overall pass/fail. New prompt + parser
(`_parse_consistency`) emit/read:

```json
{
  "consistent": true/false,
  "mismatches": [ {"image_index": <1-based index among marketing photos>, "issue": "..."} ]
}
```

- `image_index` is 1-based over the marketing photos in the order sent (image 0
  is the delivery reference and is never numbered). `ContentQualityResult` gained
  a `mismatches: List[dict]` field.
- **Back-compatible:** `_parse_consistency` still accepts the old overall schema
  (`matches_intended_content` / `specific_issues`) that existing test doubles
  emit, so the schema swap can't silently flip verdicts. (step-96 / step-98
  consistency tests still pass untouched.)

### Step 2 — targeted regeneration with corrective feedback (`pipeline_orchestrator.py`)

`_stage_marketing_consistency` rewritten. On a mismatch, for **each** reported
`image_index`:

1. Regenerate **only that** marketing image via the new
   `ProductImageAgent.regenerate_listing_image(slot, corrective_guidance, filename)`,
   which appends corrective guidance to the hero/lifestyle prompt and overwrites
   the file in place (path/catalog entry stay stable). The guidance feeds back
   the vision model's own `issue` text **plus** the delivery asset's ground-truth
   design description (reused from the `visual_brief` the delivery asset was
   generated from): *"this marketing image MUST depict the SAME design as the
   delivered product … real design is: {visual_brief} … rejected because:
   {issue} … do NOT show a different design/pattern/border/artwork/text."*
2. The delivery asset is **never** regenerated (confirmed correct) — even if the
   vision model flags the prepended delivery photo, `_resolve_mismatch_targets`
   skips any target whose path is the delivery asset.
3. Each regenerated image runs the **same gates** any fresh image does —
   `ImageValidationService` **and** `ContentQualityService.review_asset_file` —
   a targeted retry doesn't get to skip them. A regen that fails either gate is
   discarded (old image kept) so the re-check still sees the mismatch.
4. Re-run `check_marketing_consistency()` on the full updated set.
5. Pass → proceed normally. Still failing → repeat with the **new** mismatch
   feedback, up to `settings.MARKETING_CONSISTENCY_MAX_REMAKES` total remakes
   **per task** (not per image).
6. Cap exhausted → fall back to **exactly today's behavior**:
   `BLOCKED_NO_PRODUCT`, alert, no listing created.

### `MARKETING_CONSISTENCY_MAX_REMAKES = 2` (`config/settings.py`)

Default **2**. Two attempts give the feedback loop a real chance to converge
(one to correct the flagged image, one more if the first remake also drifts)
while hard-capping worst-case regeneration cost. It is a per-**task** budget, so
it cannot spiral regardless of how many images are flagged.

## Step 3 — cost discipline (real worst-case added cost per product)

Only the generated marketing photos (hero + lifestyle = **2** images) are ever
regenerable; the delivery asset never is. So the worst case is both marketing
images wrong on both attempts:

| Item (per remake attempt, worst case) | Unit | Qty | Cost |
|---|---|---|---|
| Regenerated images (Seedream, flat) | $0.040 | 2 | $0.080 |
| Content-quality vision review of each regen | ~$0.004 | 2 | $0.008 |
| One consistency re-check vision call | ~$0.006 | 1 | $0.006 |
| **Per attempt** | | | **~$0.094** |

**× 2 attempts (the hard cap) ⇒ ~$0.19 worst-case added per product.**

- Typical case (a single mismatched image, e.g. one bad preview): ~$0.05/attempt
  → ~$0.10 worst case.
- This is bounded by the cap exactly as required: no scenario exceeds roughly
  `2 × (regenerate mismatched images + re-check)`. With `MAX_REMAKES` unchanged
  there is **no** unbounded-regeneration path — a stubborn mismatch blocks, it
  doesn't keep spending.
- The initial consistency check is unchanged/pre-existing; the figures above are
  the *added* cost only, on top of the per-product estimates from steps 91/96.

## Fallback safety net preserved

The original hard block still fires when remakes are exhausted or disabled:
- New test **[2]**: a mismatch persisting through both remakes →
  `BLOCKED_NO_PRODUCT`, **zero** Etsy listings, and **exactly 2** remake attempts
  (cap proven, not exceeded).
- New test **[3]**: `MARKETING_CONSISTENCY_MAX_REMAKES = 0` → immediate block with
  **zero** remakes (original step-96 behavior intact).
- Existing step-96 test [5] and step-98 consistency tests unchanged and passing.

## Tests

`scripts/test_step100b_consistency_remake.py` (4/4, fake vision + fake image
agent, zero real API calls / zero generation cost):
- **[1]** fail-then-pass: only the mismatched hero is remade (not the delivery
  asset, not the correct lifestyle image), the vision issue text + ground truth
  are in the corrective guidance, and the task proceeds to create a listing.
- **[1b]** the real `ProductImageAgent.regenerate_listing_image` embeds the
  corrective guidance into the actual generation prompt.
- **[2]** cap enforced + hard-block fallback (above).
- **[3]** remakes-disabled safety net (above).

Full regression: steps 89–96, 98, 100b all green.
