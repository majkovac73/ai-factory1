# Step 100k — PDF planners: listing photos from the real pages (stop the block)

**Date:** 2026-07-09

## Problem

`pdf_planner_or_guide` was the last format still on the independent-generation
path: its hero/lifestyle listing photos were a separate text-to-image generation
that depicted DIFFERENT pages than the delivered PDF, so the consistency gate
correctly rejected them and the task blocked — the same root cause coloring pages
had (step 100g). Step 100f only changed the prompt wording; it didn't fix the
root cause.

## Fix — derive PDF listing photos from the ACTUAL pages

`derive_listing_from_delivery` now covers **all digital formats** (was digital
single-image only), so PDF also skips independent generation and the consistency
gate (there is no independent image to misrepresent).

`_build_listing_mockups` detects a PDF delivery and builds the listing photos from
the **real extracted pages**:

- `_extract_pdf_pages(pdf, max_pages=4)` pulls the real page images from the
  Pillow-assembled PDF via pypdf (same structure content-quality's
  `_delivery_image_bytes` relies on) to temp PNGs.
- **hero.png** — the first real page on a desk (`build_mockup_bytes(role="flatlay")`).
- **lifestyle.png** — a **fan of up to 4 real pages** on a desk, each foreshortened
  + rotated at its own angle with soft shadows (new
  `MockupService.build_flatlay_bytes`). This is the classic multi-page planner
  listing shot — honest (real pages), attractive, and angled so a screenshot
  isn't a usable flat copy.

The raw PDF is uploaded only as the buyer-gated digital file, never as a public
listing photo (same guarantee as the single-image path). POD keeps its
independent-generation path (its photos are real Printify product mockups).

## Effect

PDF planners now complete instead of blocking: no independent generation, no
consistency conflict, listing photos that genuinely show the delivered pages, and
2 fewer Seedream calls per PDF product. Visually confirmed: 4 planner pages fanned
out on a desk at natural angles.

## Tests

`scripts/test_step100k_pdf_mockups.py` (3/3): `_extract_pdf_pages` pulls the real
pages; `build_flatlay_bytes` composes a valid, angled multi-page mockup;
`_build_listing_mockups(pdf)` builds two page mockups (roles
`pdf_page`/`pdf_fan`), registered as `listing`, with the raw PDF never a photo.
Full regression green (17 suites; step-98's PDF consistency-service unit test is
unchanged — the service still works, the pipeline just no longer needs it for PDF).
