# Step 100l — Tighten PDF planner page-content QA

**Date:** 2026-07-09

## Problem

After 100k unblocked PDF planners, a real run (task 3000c3a9) revealed the
remaining weak link: the generated **page content** had Seedream quirks the
generic per-page QA let through — a decorative **photo** (a pineapple) on a
meal-planner page, and **garbled meta-text** ("Print-Iready … Design Page 4 of
6"). These would go straight to a live listing under auto-publish.

## Fix — two levers

**1. Generation prompt (`PDFGenerationService._build_page_prompt`)** now hard-
steers to a clean functional layout: headings, tables/grids, lists, ruled lines,
checkboxes, generous white space, high-contrast black line-work on solid white,
with **only** short correctly-spelled labels — and explicitly **no** photographs/
decorative imagery/clip-art, **no** paragraphs of body text, **no** page numbers/
"page X of Y"/"print-ready"/shop-or-product name/watermark. This kills both
failure modes at the source (the random photo and the rendered meta-text that
garbles).

**2. Strict per-page reviewer (`ContentQualityService.review_pdf_page_bytes`)** —
a dedicated, harsher review for planner/guide pages that FAILS a page if it:
- is a **photograph** or has decorative/clip-art imagery instead of a clean
  functional layout, or content unrelated to the page's purpose (→
  `matches_intended_content=false`);
- has **any** garbled/misspelled/duplicated/cut-off word (e.g. "Print-Iready"),
  or stray printed meta-text like "page 4 of 6" (→ `text_coherent=false`).

`PDFGenerationService._review_page` now prefers this strict reviewer per page
(falling back to the generic asset review only for older test doubles). A page
that fails is regenerated up to `CONTENT_QA_MAX_ATTEMPTS`, then the whole PDF
fails (task blocks rather than shipping a bad planner) — the existing per-page
gate, just stricter. New `settings.PDF_QA_MODEL` (default = the cheap vision
model) lets a stronger reader be swapped in for PDF pages without touching the
single-image gate.

## Tests

`scripts/test_step100l_pdf_page_qa.py` (4/4): the strict reviewer fails a
photo/garbled page and passes a clean one; the review prompt names the right
rejections (photos, garbled/misspelled text, "page x of y", functional layout);
the generation prompt forbids imagery + meta-text and asks for a functional
print-ready layout; and `PDFGenerationService` uses the strict reviewer for every
page. Full regression green (18 suites).

## Effect on reliability

This closes the PDF quality gap flagged in 100k: a clean-layout prompt plus a
strict per-page gate means a planner page with a random photo or garbled text is
regenerated or the product blocks — it doesn't auto-publish. PDF is now much
safer for unattended running. (Note: no vision QA is perfect at spotting a single
subtle typo; `PDF_QA_MODEL` can be bumped to a stronger reader if needed.)
