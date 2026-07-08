
## Step 91 — Root-cause fixes (Steps 0/0B) + real PDF/multi-image product capability
**Date:** 2026-07-08

---

### Step 0 — root cause of the "second bad listing"

Traced using Railway deployment history, log retention per-deployment, and
the live production `/tasks` + `/analytics/events` API (5 tasks total exist
in production).

**Finding: it did NOT happen after the gate fix deployed — it happened
before.**

| Event | Timestamp (UTC) |
|---|---|
| Task `97f0e7a0` ("Eco-Friendly Digital Downloads" — the second bad listing) completed | 05:08:49 |
| Gate fix committed (`bddd576`) | 05:37:33 |
| Gate fix first live on Railway (deployment `fb12c0f8`) | 05:37:44 |
| Task `7941465b` ("AR Home Decor Visualizer" — first task to run under the fix) completed | 05:57:10 |

`97f0e7a0` finished 29 minutes before the fix was even committed. It ran
entirely on the pre-fix code, which unconditionally created listings. The
one task that DID run after deployment (`7941465b`) was correctly caught —
its `output_data` shows `pipeline_status: BLOCKED_NO_PRODUCT`. There is no
regression in the deployed gate logic.

**However, this investigation surfaced two real, live bugs the prior gate
fix didn't cover, both fixed in this change:**

1. **`POST /tasks/etsy/listing` hardcoded `type="seo_writing"`**
   (`app/api/routes/tasks.py`). Since `"seo_writing"` was never `"pod"` or
   `"digital_download"`, the entire hard gate was skipped for it — straight
   through to `create_draft_listing()` with zero asset verification, the
   original bug through a different door. Confirmed live: task `8421ddb3`
   ("Etsy planner for coffee lovers") went through exactly this path.
   **Fixed** by making the gate default-deny (see Fix section below) — any
   `task.type` that isn't a recognized `product_format` now skips the
   entire pipeline, not just the parts that happened to check for it.
2. **`EtsyImageService.publish_listing()` called the wrong Etsy endpoint** —
   `PATCH /v3/application/listings/{listing_id}` instead of
   `PATCH /v3/application/shops/{shop_id}/listings/{listing_id}` (every
   other method in that file is shop-scoped; this one wasn't). Production
   has `AUTO_PUBLISH_LISTINGS=true`, so this fired for real and produced the
   exact 404 recorded in task `7941465b`'s blocked reason. **Fixed** — this
   was silently guaranteeing every digital product would fail at publish
   and get rolled back, regardless of gate/concept quality.

### Step 0B — why product_type never resolved to POD

`AUTONOMY_ENABLED=false` in production the entire time (confirmed via
`railway variables` and worker startup logs) — the autonomous loop has
**never run on a schedule**. The 3 pre-fix `general`-type tasks were
produced by manual service restarts (the worker runs one cycle immediately
on thread start). There's exactly one real sample of the fixed schema
running (`7941465b` → `digital_download`), so "always digital" wasn't yet
statistically established — but there was also no structural mechanism
forcing format variety.

`ProductTypeSelectorAgent` **is** correctly wired into the real completion
flow (`PODFulfillmentService.create_product_for_task()` →
`PipelineOrchestrator._stage_printify_precheck()`) — it simply had never
been exercised because no task had ever resolved to a POD type.

More fundamentally: `7941465b`'s concept ("AR Home Decor Visualizer...
utilizes advanced AR capabilities... Purchase Integration: direct links to
purchase art pieces from the app") described an **interactive AR software
application** — something no amount of image generation could ever
produce. The prior fix's validation only rejected vague strategy language;
it never checked whether the concept was buildable at all. This is what
Step 2 (below) closes structurally.

---

### Fix — default-deny gate + strict product_format allow-list

**`app/core/product_formats.py`** (new) — single source of truth, imported
by both `TrendResearchAgent` and `PipelineOrchestrator` so they can't drift:

```
single_print, coloring_page, greeting_card_design, phone_wallpaper   → digital, single image
sticker_sheet_design                                                 → digital, single image (one sheet)
pdf_planner_or_guide                                                 → digital, multi-page PDF
pod_apparel_design                                                   → pod,     single image (+ listing photos)
```

**`app/services/pipeline_orchestrator.py`** — `task.type` must be one of
the formats above or the ENTIRE pipeline is skipped (no listing_images, no
delivery asset, no `create_draft_listing()` — nothing). This closes the
`seo_writing`-bypass bug structurally rather than per-type. For recognized
formats, delivery-asset generation branches on format
(`PODPipelineService` for single images, `PDFGenerationService` for PDFs),
and the hard gate now also covers PDF page-count readback and
Printify-product readback (see below).

**`app/agents/trend_research_agent.py`** — the LLM must pick a real
`product_format` from the list above for a proposed concept, with strict
validation: vague strategy language, bundle/set/kit/collection language
(multi-item), and any concept whose format doesn't match one of the 7 real
options are all rejected and retried (up to 3 attempts). `pdf_planner_or_guide`
concepts must also supply a `page_count` ≤ `MAX_PDF_PAGES`, checked at the
concept stage before a task (and any spend) is ever created.

**`app/workers/autonomy_worker.py`** — passes `product_format` as `task.type`
and `page_count` through task metadata for PDF concepts.

---

### Feature — real PDF products (Step 1)

**`app/services/pdf_generation_service.py`** (new) — generates one real
image per page via `ImageProviderManager` (same provider/cost model as
every other delivery asset — a 6-page PDF is 6 real, billable
image-generation calls, not a trick) and assembles them into a genuine
multi-page PDF.

- **Library choice: Pillow only for generation** (`Image.save(...,
  format="PDF", save_all=True, append_images=[...])` + `ImageDraw` for a
  short caption per page). Pillow was already a hard dependency; pages here
  are image-centric with a short caption, not flowing body text, so
  reportlab/fpdf2 weren't needed for generation.
- **`pypdf` added for readback only** (Pillow cannot read PDFs back). After
  assembly, the file is independently re-opened via `pypdf.PdfReader` and
  its actual page count is compared against what was requested — this is
  the "confirm it opens correctly and has the expected page count" gate
  requirement, not just a `Path.exists()` check.
- **`settings.MAX_PDF_PAGES = 6`** (hard cap, enforced in both
  `TrendResearchAgent` at concept time and `PDFGenerationService` itself as
  defense-in-depth). Chosen at the lower end of the suggested 4–6 range —
  see cost note below.
- **Partial failure = total failure.** If any page's image generation
  fails mid-sequence, `PDFGenerationService` raises immediately; no partial
  PDF is ever assembled or registered. Verified in
  `test_step91_pdf_and_formats.py` [3]: a 3-page concept where page 3 fails
  results in zero delivery asset, zero listing creation.

---

### Feature — extended readback gate (Step 3)

- **PDF**: page-count readback via `pypdf` (above).
- **POD**: `PODFulfillmentService.create_product_for_task()` now re-fetches
  the product from Printify (`PrintifyClient.get_product()`, new) after
  creation and confirms the submitted `image_id` is actually present in a
  print-area placeholder — not just that `create_product()` returned 200.
- **Listing photos (all formats)**: after `attach_images_and_publish()`
  reports images uploaded, `EtsyImageService.get_listing_images()` (new)
  re-fetches the listing's actual image gallery from Etsy and confirms the
  count. If any of these post-listing checks fail — digital file upload,
  Printify readback (pre-listing, so it blocks outright), or listing-image
  readback — the listing (which by Etsy's API shape must already exist
  before files/images can be attached to it) is deleted via
  `EtsyClient.delete_listing()` and the task is marked
  `BLOCKED_NO_PRODUCT`.

---

### Tests

- `scripts/test_step89_pipeline_orchestrator.py` and
  `scripts/test_step90_product_gate.py` — updated from the old
  `general`/`pod`/`digital_download` vocabulary to real `product_format`
  values (`single_print`, `pod_apparel_design`), since default-deny means
  unrecognized types no longer flow through the pipeline at all. Added a
  case proving an unrecognized type is skipped entirely (9/9 and 6/6 pass).
- `scripts/test_step88_autonomy.py` — updated to the `product_format`
  schema (8/8 pass).
- `scripts/test_step91_pdf_and_formats.py` (new, 6/6 pass) — successful
  single-image product, successful 3-page PDF (with real Pillow assembly +
  real pypdf readback), forced partial PDF failure (page 3/3 fails → zero
  listing), successful multi-image POD product (both Printify and Etsy
  readback pass), forced Printify readback failure (→ zero listing), forced
  Etsy listing-image readback failure (→ listing created then deleted).
- `scripts/test_step81_pod_fulfillment.py`, `test_step83_stress.py`,
  `test_step84_performance.py` — fake Printify clients updated with
  `get_product()` so the new readback check doesn't break their doubles.

All suites pass with zero real Etsy/Printify/OpenRouter API calls.

---

### Cost note

Real confirmed image cost (Seedream 4.5, current `OPENROUTER_IMAGE_MODEL`):
**$0.04/image flat**, regardless of resolution or aspect ratio.

| Product format | Images | Cost |
|---|---|---|
| Single-image digital (existing) | 2 listing + 1 delivery + 1 Pinterest pin = 4 | $0.16 |
| `pdf_planner_or_guide` at `MAX_PDF_PAGES=6` | 2 listing + 6 PDF pages + 1 Pinterest pin = 9 | **$0.36** |
| `pod_apparel_design` | 2 listing + 1 delivery + 1 Pinterest pin = 4 | $0.16 (+ Printify's own per-item cost, unrelated to image generation) |

A maxed-out PDF product costs **2.25× a single-image product**. Against
`MAX_DAILY_SPEND_USD=5.00`, that's a ceiling of ~13 PDF products/day if
every autonomy cycle happened to pick the PDF format (in practice
`MAX_TASKS_PER_DAY=10` binds first). Flagging for Maj: `AutonomyWorker`'s
pre-flight spend check (`estimated_max = 0.30` per cycle,
`app/workers/autonomy_worker.py`) predates PDF support and does **not**
yet account for the PDF format's higher per-product cost — it will still
gate correctly against the *daily* cap (spend is recorded after the fact
either way), but the per-cycle estimate used for the go/no-go check is now
understated for PDF concepts specifically. Not fixed here since it's a
pre-flight estimate refinement, not a correctness bug — worth a follow-up
if PDF products end up being a large share of autonomy output.

---

### Cleanup

Task `97f0e7a0` (the second bad listing) predates this fix and is not
retroactively marked `BLOCKED_NO_PRODUCT` — there's no safe write path to
production from this environment (no registered Railway SSH key, no admin
API for `output_data`), and the task is already terminal (`DONE`) and won't
be reprocessed. Maj is deleting the actual Etsy draft listing manually, per
the prior fix's cleanup note — that remains the authoritative fix for what
matters (nothing fake-but-real stays live on Etsy).
