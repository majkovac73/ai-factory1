# Step 97 — Fix: PDF page generation requested an image size below Seedream's pixel floor

**Date:** 2026-07-09

---

### Symptom (real production failure)

Real autonomy run, task `127d5130` ("Mindfulness Daily Planner",
`pdf_planner_or_guide`, 6 pages), failed at **page 1/6** with:

```
OpenRouter Image API error 400: "The parameter `size` specified in the
request is not valid: image size must be at least 3686400 pixels."
```

Because a partial PDF is never assembled (any page failure = whole-task
failure by design), the task was blocked before a single page rendered.

### Root cause

`PDFGenerationService` requested every page at **`aspect_ratio="2:3",
resolution="2K"`**. Seedream 4.5 rejects any request below **3,686,400
pixels**. For a portrait 2:3 page, "2K" allocates only **1365×2048 =
2,795,520 px** — below the floor — so the very first page 400'd.

This is the **exact same bug already fixed for the Pinterest pin** in step
"Switch default image model to Seedream 4.5" (`social_image_agent.py`:
`PINTEREST_RESOLUTION` 2K→4K, same 2:3 ratio, same floor). The PDF service
was written in step 91 with a hardcoded `"2K"` and never got the same
treatment. It never surfaced in tests because step 91's `FakeImageProvider`
ignores `resolution`/`aspect_ratio` and always returns a valid image.

**Correction to the step-91/memo assumption:** the memo estimated PDF pages
at "1K". They were actually being requested at **2K** — still below the
floor for 2:3, just for a different reason than assumed. Either way the
per-image cost is unaffected (see cost note).

### Fix

**`app/services/pdf_generation_service.py`**

- Added module-level constants mirroring `social_image_agent.py`'s idiom:
  - `PDF_PAGE_ASPECT_RATIO = "2:3"`
  - `PDF_PAGE_RESOLUTION = "4K"`  ← **exact change: `"2K"` → `"4K"`**
- The per-page `generate_image(...)` call now uses these constants instead
  of the inline `aspect_ratio="2:3", resolution="2K"`.

**2:3 @ 4K = 2732×4096 = ~11.2M px**, comfortably above the 3,686,400 floor.
"4K" is the smallest standard tier that clears the floor for a 2:3 page
("2K" is ~2.8M, below it). This matches the size already proven against the
real Seedream API for the Pinterest pin (2:3 @ 4K, real cost $0.04).

### Cost impact — none

Seedream 4.5 is **flat-rate $0.04/image regardless of resolution or aspect
ratio** (confirmed by real API calls in `CHANGELOG_AUTOMATED.md`: both
1:1@2K and 2:3@4K measured at exactly $0.04). Moving PDF pages from 2K to 4K
therefore does **not** change any cost assumption:

| Product format | Images | Cost (before) | Cost (after) |
|---|---|---|---|
| `pdf_planner_or_guide` @ `MAX_PDF_PAGES=6` | 2 listing + 6 pages + 1 pin = 9 | $0.36 | **$0.36 (unchanged)** |

The step-91 `MAX_PDF_PAGES` cost model and the `MAX_DAILY_SPEND_USD` ceiling
are unaffected. (The pre-flight `estimated_max` under-count for PDF concepts
flagged in step 91 remains a separate, still-open follow-up — unrelated to
this size fix.)

### Tests

**`scripts/test_step97_pdf_resolution.py`** (new, 4/4 pass). Uses a
`ResolutionAwareFakeProvider` that reproduces the **exact** Seedream 400 for
any request below 3,686,400 px (deriving width×height from the tier's long
edge + aspect ratio, per the real dimensions recorded in the changelog):

- **[1]** Proves the simulation is real: the old setting (2:3 @ 2K =
  2,795,520 px) is genuinely rejected with the exact 400 message — the fake
  is not a no-op that would pass regardless.
- **[2]** Runs the actual (fixed) `PDFGenerationService` against that same
  strict provider: all pages clear the floor, no 400 is raised, and a real
  multi-page PDF is assembled and readback-verified (real Pillow assembly +
  real `pypdf` page-count check). Also asserts every page was requested at
  `4K`.
- **[3]** Safety net preserved: a genuine (non-size) generation failure
  still fails the whole PDF on page 1 — no partial output. (The equivalent
  end-to-end safety net in `test_step91_pdf_and_formats.py` [3] also still
  passes: 6/6.)

Both suites pass with zero real API calls.

### Real post-fix verification (done — this fix confirmed working in prod)

Deployed to Railway (commit `625a207`; verified `PDF_PAGE_RESOLUTION="4K"`
present in the running container via `railway ssh`) and triggered a real
`pdf_planner_or_guide` task in production — the same "Mindfulness Daily
Planner", 6 pages, that failed as task `127d5130`. New task:
`3c5180db-5443-418f-9b75-a3ced5aa78f0`.

**The size fix works.** Logs show all image-generation calls returning
`200 OK` (no more `400 image size must be at least 3686400 pixels`), and:

```
PDFGenerationService: generated and verified 6-page PDF for task
3c5180db-...: /data/images/delivery/3c5180db-.../design.pdf
```

All 6 pages generated, the PDF was assembled and independently
readback-verified (page count = 6), and the per-page content-QA gate
passed. That is exactly the confirmation this step required.

**Separately discovered blocker (NOT this fix — a distinct, pre-existing
bug this run surfaced now that PDFs get far enough to hit it):** the
pipeline then blocked at `BLOCKED_NO_PRODUCT` in the *marketing/deliverable
consistency* gate. `ContentQualityService.check_marketing_consistency()`
sends `delivery_path.read_bytes()` to a vision model as an image
(`_image_to_data_url`). For a PDF deliverable those are PDF bytes — Pillow
can't open them, so `_downscale_for_review` falls back to raw PDF bytes
labeled `image/png`, which the vision provider rejects with
`invalid_image_format` ("unsupported image ... [jpeg, webp, gif, png]").
This blocks **every** PDF product from ever publishing. No Etsy listing was
created (`etsy_listing_id: None`) — the block is upstream of listing
creation, so nothing to clean up on the store. Fix needed: rasterize the
PDF's first page to PNG before the consistency vision call (or skip/relax
the consistency check for PDF deliverables). Tracked as the step-98
follow-up.
