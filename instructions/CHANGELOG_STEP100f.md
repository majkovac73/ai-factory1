# Step 100f — pdf_planner_or_guide marketing images depict real page content, not a generic book cover

**Date:** 2026-07-09

## Problem

Real prod task **b68b099a** (5-page PDF) was blocked by the marketing/deliverable
consistency gate: its hero/lifestyle images showed "a book cover with decorative
elements" bearing no relation to the actual page content inside the delivered
PDF. This is the **same class** as the coloring_page issue fixed in step 100d —
the generic marketing prompt produces imagery that can never structurally match
this format's real deliverable (real interior planner/guide pages), so no number
of remakes converges.

## Fix — a second branch on the existing per-format structure

Step 100d already established the per-format branching in
`ProductImageAgent._format_override(product_format, role, product_name,
visual_brief)`. This adds **`pdf_planner_or_guide`** as a second branch on that
same structure (not a one-off), and threads a new `content_context` argument so
the prompt is grounded in the **real generated page topics** rather than just the
product name.

- **content_context**: the orchestrator's new `_marketing_content_context(is_pdf,
  output_data)` joins the actual generated page topics (`output_data["sections"]`,
  the same briefs `PDFGenerationService` renders) into a short string. It is
  threaded end-to-end: `run_post_completion` → `_stage_listing_images` /
  `_stage_marketing_consistency` → `_regenerate_marketing_image` →
  `generate_listing_images` / `regenerate_listing_image` → the prompt builders. So
  both the initial generation **and** the consistency-remake path are grounded in
  real content. (Note: the image provider is text-to-image, so the real page
  *images* can't be fed as pixels; grounding is via the real page *content/topics*
  in the prompt — the achievable equivalent of "reference the real pages.")
- Empty `content_context` for non-PDF formats (they ignore it), so nothing else
  changes.

### Before → after (pdf_planner_or_guide)

**Hero — before (generic, produced the "decorative book cover"):**
> Professional product photography style. Hero shot of: {product_name}. Visual
> brief: {visual_brief}. Clean, high-quality image suitable for an online
> marketplace listing. No text, no watermarks, no borders.

**Hero — after (pdf_planner_or_guide):**
> An overhead flatlay of the ACTUAL printed interior pages of the multi-page
> '{product_name}' planner/guide PDF, arranged on a desk — show one or two real
> INTERIOR pages with their actual layout visible (headings, lists, tables, and
> writing/fill-in lines), exactly as the pages look when printed. This is NOT a
> closed book and NOT a decorative book cover — show the real page content the
> buyer receives inside the PDF. Theme/brief: {visual_brief}. The real interior
> pages cover: {content_context}. Bright, even lighting, clean desk, no watermarks.

**Lifestyle — before (generic):**
> Lifestyle photography style. Context/in-use shot of: {product_name}. Visual
> brief: {visual_brief}. Warm, aspirational atmosphere. No text, no watermarks.

**Lifestyle — after (pdf_planner_or_guide):**
> A realistic in-use photo of a person writing on / filling in one of the ACTUAL
> interior pages of the '{product_name}' planner/guide, pen in hand, with the real
> page layout (headings, lists, writing lines) clearly visible on the desk. It
> must show a real INTERIOR page being used — NOT a closed book and NOT a
> decorative cover. Theme/brief: {visual_brief}. The real interior pages cover:
> {content_context}. Warm natural light, cozy desk setting, no watermarks.

`{content_context}` is the real generated page topics, e.g. *"Weekly Menu Grid;
Grocery Shopping List; Meal Prep Tips; Pantry Inventory; Budget Tracker"*.

Only `coloring_page` and now `pdf_planner_or_guide` are special-cased; every other
format keeps the generic photography prompts.

## Tests

`scripts/test_step100f_pdf_planner_prompts.py` (4/4), fake image provider
capturing the exact prompts (no real API/generation cost):
- **[1]** `generate_listing_images(product_format="pdf_planner_or_guide",
  content_context=<real topics>)` → hero AND lifestyle reference the real INTERIOR
  pages / actual page topics and explicitly reject "book cover".
- **[2]** the pdf prompts are DISTINCT from both the generic default and the
  coloring_page template (no line-art/uncolored language; no generic photography
  framing).
- **[3]** the **remake** path is also pdf-aware (interior-page framing + real
  topics + corrective guidance all present).
- **[4]** the orchestrator grounds `content_context` in the real page topics for
  PDFs (`_marketing_content_context`), and returns "" for non-pdf / no sections.

Full regression re-run: steps **69, 89–96, 98, 100b, 100d, 100f** all green.

## Note — this is the second format needing this correction

`coloring_page` (100d) and now `pdf_planner_or_guide` (100f) have both needed a
per-format marketing-prompt correction because their deliverables are structurally
unlike a generic product photo. The remaining formats — single_print,
phone_wallpaper, greeting_card_design, sticker_sheet_design, pod_apparel_design —
are plausibly fine with the generic prompt (they *are* essentially a printed/worn
image of the design), but they should be **spot-checked for the same structural
mismatch** before assuming so, e.g. sticker_sheet_design (a sheet of many
die-cut stickers vs. one hero image) is the most likely next candidate. The
`_format_override` dispatch makes adding any of them a small, localized branch.

## Verify (pending, cheap)

Once deployed, a real `pdf_planner_or_guide` task's marketing images should show
real interior pages and pass the consistency gate on the first check (no
remake/block loop). Real deploy + live confirmation remains pending Maj's go-ahead
(git push to prod), consistent with 100c–100e.
