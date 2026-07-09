# Step 100c — Investigate consistency-remake on prod task 8d9f8e58 + harden the mismatch mapping

**Date:** 2026-07-09

## Step 0 — Did the remake logic even run? YES (this is not a pre-deploy artifact)

Investigated real prod task **8d9f8e58** (`coloring_page`, "Personalized Family
Member Portrait Coloring Page") via `railway ssh` against the production DB.

| Fact | Value (UTC) |
|---|---|
| step-100b commit `5a2219f` | 11:48:50 |
| deploy `5b00e055` created | 11:48:57 |
| task 8d9f8e58 ran | 11:50:09 → 11:51:57 |
| **corrective-guidance regenerations logged** | **11:51:08, 11:51:20, 11:51:40** |

The 3 log rows are `ProductImageAgent :: "Listing image regenerated with
corrective guidance"` — a message + method (`regenerate_listing_image`) that
**only exist in step-100b**. So the new remake code was live and **did fire**.
Their payloads show exactly:

```
regen -> hero      | hero.png       (round 1)
regen -> lifestyle | lifestyle.png  (round 1)
regen -> hero      | hero.png       (round 2)
```

So the remake worked **mechanically and correctly**: for this digital format the
marketing set sent to the vision model was `[delivery(1), hero(2), lifestyle(3)]`,
the mismatch was on images **2 and 3**, and `_resolve_mismatch_targets` mapped
those to hero + lifestyle and regenerated them across the 2-remake cap (round 1
fixed lifestyle, round 2 retried hero, which still failed the re-check). It then
hit the cap and fell back to the hard block.

**Conclusion:** the premise "the remake didn't fire" is disproven. It fired and
correctly targeted the right images. Task 8d9f8e58 blocked because the
regenerated *marketing* photos still couldn't be made to match the delivered
*coloring-page line-art* within the cap — a **convergence** limitation, not a
mapping bug. The cap + hard block behaved as designed and correctly prevented a
buyer-misrepresenting listing.

> Root-cause nuance vs. the task's hypothesis: the "hardcoded 2-image / unmapped
> index 2-3" theory was **not** what bit this task — the delivery asset is
> *prepended* for digital formats, so the list already had 3 entries and indices
> 2/3 mapped fine. But the investigation surfaced a **genuine latent silent
> gap** worth fixing anyway (below).

## The real latent gap (fixed here)

Two real weaknesses that would silently misbehave in other cases:

1. **Out-of-range / malformed `image_index` was silently dropped.** The old
   `_resolve_mismatch_targets` did `if not (1 <= j <= len(existing)): continue`.
   For **POD** formats the delivery asset is *not* prepended, so there are only 2
   marketing images — a vision response citing "image 3", or any hallucinated /
   malformed index, vanished with no trace and the task quietly fell through to a
   generic block. That is exactly the "looks like the feature doesn't work" vs.
   "an edge case wasn't handled" confusion the task warns about.
2. **Regeneration role was filename-hardcoded to hero/lifestyle.** Any image that
   wasn't literally `lifestyle*` was regenerated with the **hero** prompt, so a
   future format adding a third listing shot (in-use / detail crop) could not be
   regenerated with a correct prompt.

## The fix (format-agnostic + loud on the unexpected)

**`_resolve_mismatch_targets` (pipeline_orchestrator.py)** now:
- Maps `image_index` onto the **real, full marketing list actually sent to the
  vision model** (`current_paths`) — however many images the format produced
  (2, 3, 4…). No hardcoded count.
- Returns `(targets, anomalies)`. `targets` is `{index: (role, issue)}`; a per-
  image **role** (`hero` / `lifestyle` / filename-stem fallback via
  `_marketing_role_for`) is carried so each image is regenerated with the right
  prompt.
- Collects genuinely-unmappable indices (non-int / out-of-range) into
  `anomalies` instead of silently dropping them. A mismatch reported against the
  delivery asset stays a **benign skip** (it's confirmed correct), distinct from
  an anomaly.

**Loud, distinct alert for anomalies (`_alert_unmappable_mismatch`).** When an
index can't map to any regenerable asset, it now emits a `logger.error` **and**
an `AlertService` alert naming the exact index and `task_id` — a different,
alerted failure mode from the normal "images don't match" block, and recorded in
the report as `marketing_consistency.unmappable_indices`.

**`ProductImageAgent.regenerate_listing_image` is role-agnostic.** `slot` →
`role`; a role→prompt map (`_prompt_for_role`) routes hero/lifestyle to their
existing prompts and **any other role** to a new generic `_build_marketing_prompt`,
so ANY image in the listing set is regenerable by role.

## Step 3 — real worst-case cost, audited across every format

Audited **all 7** formats in `product_formats.py` (single_print, coloring_page,
greeting_card_design, phone_wallpaper, sticker_sheet_design, pdf_planner_or_guide,
pod_apparel_design). Every one generates exactly the **same 2 regenerable
marketing images** (ProductImageAgent hero + lifestyle); the delivery asset is
never regenerated. So the richest format today still has **2** regenerable
images, and the step-100b worst case is unchanged:

- Per remake round (worst case, both images): 2×$0.040 (Seedream) + 2×$0.004
  (content review) + ~$0.006 (consistency re-check) ≈ **$0.094**.
- × 2 remakes (hard cap) ⇒ **~$0.19 worst-case added per product** — bounded,
  unchanged.
- **Future-proofing:** if a format later adds an *N*-image listing set, worst
  case scales as `2 × (N×$0.044 + ~$0.006)` and remains hard-capped by
  `MARKETING_CONSISTENCY_MAX_REMAKES`. The role-agnostic path already supports
  it with no further code change.

## Step 3/tests — regression-proofed with a real multi-image test

`scripts/test_step100b_consistency_remake.py` extended (now **7/7**), covering
exactly the blind spot the 2-image-only tests missed:
- **[4]** 3-image set, mismatches on indices **2 AND 3** → each maps to its real
  asset and is regenerated by its **correct role** (hero / lifestyle) with its
  own feedback; task proceeds.
- **[5]** a genuinely **out-of-range** index → **loud AlertService alert**
  (asserted, containing the bad index), distinct from a silent fallback, while
  the mappable image in the same response is still remade.
- **[6]** the cap is **per-task** across multiple images: 2 images × 2 rounds =
  4 regenerations, then hard block, no listing.

Full regression re-run: steps **89–96, 98, 100b** all green.

## Known follow-up (not this change)

For `coloring_page`, the marketing prompts ("professional product photography" /
"lifestyle") produce colored/photographic renders that a vision model can read as
a *different design* from the blank line-art the buyer receives — so consistency
can be hard to satisfy by text-prompt remakes alone. That is a **prompt-tuning /
per-format consistency-strictness** question (make marketing shots explicitly
depict the delivered line-art, or relax the check for coloring pages), tracked as
a separate follow-up. The hard block correctly protected the shop in the meantime.

## Step 4 — verify for real: PENDING explicit go-ahead

Deploying = a git push (Railway auto-deploys to production) and triggering a real
`coloring_page` task costs real Seedream/vision spend. Both are held for Maj's
explicit confirmation before running (per cost-discipline + no unprompted
production pushes).
