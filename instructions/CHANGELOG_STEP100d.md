# Step 100d — coloring_page marketing images are honest line-art (fix the systematic consistency mismatch at the source)

**Date:** 2026-07-09

## Problem

For `coloring_page` products the deliverable is intentionally **blank line-art**,
but `ProductImageAgent` used one generic prompt template for every format:
hero = *"Professional product photography style…"*, lifestyle = *"Lifestyle
photography style… warm, aspirational atmosphere."* Those reliably produce a
**colored, finished-looking** render. A colored render can **never** match a
blank line-art deliverable, so the marketing/deliverable consistency gate
(correctly) rejected it — and no number of remakes could converge (real prod task
**8d9f8e58**: the step-100b/100c remake fired 3× and still hard-blocked). This is
a **prompt-design mismatch specific to this one format**, not a flaky-generation
issue.

The fix belongs in *what is generated*, not in *what is verified* — see the
explicit note at the bottom that the consistency check was left unchanged.

## Fix — per-format honest framing (`app/agents/image/product_image_agent.py`)

Prompt construction previously did **not** branch by format at all (one template
for everything). Added a small dispatch, `_format_override(product_format, role,
…)`, consulted by both `_build_hero_prompt` and `_build_lifestyle_prompt`. A
format with an entry there overrides the generic prompt; every other format is
untouched. Structured so adding the next format is a localized `if` branch, not a
rewrite.

`product_format` is now threaded end-to-end:
`run_post_completion` → `_stage_listing_images` / `_stage_marketing_consistency`
→ `_regenerate_marketing_image` → `ProductImageAgent.generate_listing_images` /
`regenerate_listing_image` → the prompt builders. So **both** the initial
generation and the consistency **remake** path are format-aware.

### Before → after (coloring_page)

**Hero — before (generic):**
> Professional product photography style. Hero shot of: {product_name}. Visual
> brief: {visual_brief}. Clean, high-quality image suitable for an online
> marketplace listing. No text, no watermarks, no borders.

**Hero — after (coloring_page):**
> The ACTUAL blank line-art coloring page for '{product_name}', shown clearly and
> flat on a plain white background exactly as it will look when printed: crisp
> solid black outline line art on white paper, completely UNCOLORED. Design of the
> line art: {visual_brief}. It is a COLORING PAGE — do NOT color it in, do NOT show
> a finished, painted, shaded or colored-in version; outlines only, white interior.
> No watermarks.

**Lifestyle — before (generic):**
> Lifestyle photography style. Context/in-use shot of: {product_name}. Visual
> brief: {visual_brief}. Warm, aspirational atmosphere. No text, no watermarks.

**Lifestyle — after (coloring_page):**
> A realistic context photo of the printed blank line-art coloring page
> '{product_name}' lying on a table next to colored pencils and crayons, with the
> page itself still clearly UNCOLORED crisp black-outline line art (optionally a
> child's hand just beginning to color one small corner, while the rest of the page
> remains blank line art). Design of the line art: {visual_brief}. The coloring
> page must stay recognisable as the blank line-art product the buyer actually
> receives — NOT a fully colored-in picture. No watermarks.

The lifestyle prompt is deliberately realistic **context** around the real,
still-uncolored product (pencils/crayons on a table, a hand just starting) rather
than a fictional finished version — honest, and consistent with the delivered
line-art.

Only `coloring_page` is special-cased in this change; all other formats
(single_print, greeting_card_design, phone_wallpaper, sticker_sheet_design,
pdf_planner_or_guide, pod_apparel_design) keep the existing generic photography
prompts.

## The consistency check was NOT changed or weakened

`app/services/content_quality_service.py` is **untouched** (`git diff` empty).
`check_marketing_consistency` still fails on a genuine "buyer sees X, receives Y"
mismatch exactly as before — that check is correctly catching a real problem, so
the fix is in the generated imagery, not in loosening verification. The existing
consistency/remake tests (step-96, step-100b) are unchanged and still pass.

## Tests

`scripts/test_step100d_coloring_page_prompts.py` (5/5), a fake image provider
capturing the exact generation prompts (no real API/generation cost):
- **[1]** `generate_listing_images(product_format="coloring_page")` → hero AND
  lifestyle prompts use line-art / uncolored / blank framing and NOT the generic
  "professional product photography" / "lifestyle photography" language.
- **[2]** `single_print` → generic photography prompts, with **none** of the
  coloring-page framing (proves per-format, not global).
- **[3]** `product_format=None` → generic prompts (default unchanged).
- **[4]** the **remake** path (`regenerate_listing_image`) is also format-aware for
  coloring_page — line-art framing AND the corrective guidance both present.
- **[5]** the remake path for `single_print` stays generic (distinct).

Full regression re-run: steps **69, 89–96, 98, 100b, 100d** all green.

## Expected effect

A coloring_page's hero/lifestyle images now depict the actual uncolored line-art
(and honest context around it), which the unchanged consistency gate should
accept on the first check — eliminating the systematic remake-then-block loop that
blocked task 8d9f8e58, without weakening the misrepresentation guard for any
format. Real end-to-end confirmation (deploy + a live coloring_page run, which
costs Seedream/vision spend) remains pending Maj's go-ahead, same as step-100c.
