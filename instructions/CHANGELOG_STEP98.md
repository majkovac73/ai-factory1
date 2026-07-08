# Step 98 — Fix: marketing/deliverable consistency gate rejected PDF deliverables

**Date:** 2026-07-09

---

### Symptom (surfaced by the step-97 real run)

Once the step-97 Seedream size fix let a `pdf_planner_or_guide` product
actually generate all its pages, the real prod run (task
`3c5180db-5443-418f-9b75-a3ced5aa78f0`, "Mindfulness Daily Planner") got
further and then blocked at `BLOCKED_NO_PRODUCT`:

```
ContentQualityService: consistency vision call failed: Error code: 400
"You uploaded an unsupported image. Please make sure your image has one of
 the following formats: ['jpeg', 'webp', 'gif', 'png']" (invalid_image_format)
```

No Etsy listing was created (`etsy_listing_id: None`) — the block is upstream
of listing creation. This blocked **every** PDF product from publishing.

### Root cause

`ContentQualityService.check_marketing_consistency()` sent the delivery
asset's raw bytes to the vision model as an image
(`_image_to_data_url(delivery.read_bytes())`). For a PDF deliverable those
are **PDF bytes**: `_downscale_for_review` tries `PILImage.open(...)`, which
fails (Pillow can't read PDFs), and its `except` falls back to returning the
raw bytes unchanged — so raw PDF bytes were sent labeled `image/png`, which
the vision provider rejects. The per-page content-QA gate
(`review_asset_bytes`) was fine because it receives PNG page images; only the
*whole-PDF* consistency comparison hit this.

### Fix

**`app/services/content_quality_service.py`** — new module-level helper
`_delivery_image_bytes(path)`:

- Single-image deliverables: read bytes directly (unchanged).
- **PDF deliverables:** extract the first page's embedded image via `pypdf`
  (already a hard dependency) and re-encode as PNG. Our PDFs are assembled by
  Pillow from exactly one full-page image per page (see
  `PDFGenerationService`), so page 0's single embedded image *is* the cover —
  `reader.pages[0].images[0].image` gives it as a PIL image with no PDF
  rasterizer or system library (poppler/PyMuPDF) needed.

`check_marketing_consistency` now builds the delivery data URL from
`_delivery_image_bytes(delivery)` instead of `delivery.read_bytes()`.
Marketing photos are unchanged (already images).

**No new dependencies. No cost change** (still one vision call; the delivery
image is downscaled to ≤1024px as before).

### Tests

**`scripts/test_step98_pdf_consistency.py`** (new, 3/3 pass):

- **[1]** Necessity: raw PDF bytes are *not* a decodable image (reproduces
  exactly what the provider rejected), while `_delivery_image_bytes(pdf)`
  returns decodable PNG bytes.
- **[2]** `check_marketing_consistency(pdf, [png])` against a fake vision
  provider that enforces the real "images only" rule (raises the same 400 for
  non-image bytes): the model receives only decodable images (PDF→PNG + the
  marketing PNG) and the gate passes.
- **[3]** Regression: a single-image PNG delivery is passed through untouched.

`scripts/test_step96_content_quality.py` still passes 5/5 (no regression to
the per-asset review or the unrelated-marketing rejection path).

### Real post-fix verification

- [ ] Deploy + re-run a full `pdf_planner_or_guide` cycle; confirm it now
      passes the consistency gate and publishes a real Etsy listing.
