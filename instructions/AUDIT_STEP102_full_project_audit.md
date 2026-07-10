# STEP 102 — Full Project Audit (2026-07-10)

Audit of the whole codebase with one question in mind: **what stops this project
from actually making money**, plus technical and quality issues. Ordered
most → least important. Each finding says WHAT is wrong, WHERE, WHY it matters,
and HOW to fix it — written so each can be worked as its own step with Claude Code.

Skipped by request: activating Pinterest (no API key yet — known, inactive).
Note that finding **P0-6** is still about Pinterest, but it is a *cost bug in our
pipeline*, not about activating the API.

Tests run during this audit (see Appendix A for details):
- `python -m compileall app config` → clean, no syntax errors.
- Offline suites: step100b (8/8), 100d (5/5), 100f (4/4), 100g (6/6), 100i (4/4),
  100j (3/3), seo_schema (pass) — all green.
- step100k and step100l#4 FAIL locally — **only because the local venv is missing
  `pypdf`** (also missing `pytrends`/`pandas`). See P2-10.
- `scripts/test_sanitizer.py` is broken (missing `sys.path` bootstrap), and
  `scripts/test_state_machine.py` crashes on Windows console encoding AND writes
  into the real DB. See P3-7.

---

## TIER P0 — Revenue blockers. Fix these first; until then the shop cannot earn.

### P0-3. The entire API is public and unauthenticated — strangers can spend your money
- **Where:** `app/main.py:35-41` (CORS `allow_origins=["*"]`), `app/api/api.py`
  (every router mounted with no auth dependency), `config/settings.py:54`
  (`SECRET_KEY = "change_me"`, unused).
- **What:** The Railway URL is public. Anyone who finds it can:
  `POST /tasks` (each processed task burns real OpenRouter image/text spend),
  `POST /tasks/run-pending`, `POST /tasks/{id}/process`, `POST /etsy/listings/upload`
  (creates listings in YOUR shop), `POST /marketing/post/...`, `POST /analytics/revenue`
  (pollutes your revenue data), and read `/logs` (which contain full prompts/outputs).
  This is a direct financial exposure, independent of any bug.
- **Fix:**
  1. Add a simple API-key dependency: read `FACTORY_API_KEY` from settings; a
     FastAPI dependency checks header `X-Factory-Key`. Apply it router-wide in
     `app/api/api.py` via `api_router.include_router(..., dependencies=[Depends(require_key)])`
     for every router EXCEPT `health` (Railway healthcheck must stay open) and
     the OAuth callback routes (`/etsy/oauth/callback`, `/tumblr/oauth/callback`,
     `/pinterest/oauth/callback` — external services redirect there).
  2. The dashboard frontend (`/ui`) calls the API from the browser: either keep
     read-only GET dashboards open and protect only mutating routes (minimum
     viable), or add a `?key=` prompt in the frontend that stores the key in
     localStorage and sends the header.
  3. Set a real `FACTORY_API_KEY` in Railway env; remove the unused
     `SECRET_KEY="change_me"` or repurpose it for this.
  4. Also set `DEBUG=False` / `ENV=production` in Railway (`settings.DEBUG`
     defaults to True → FastAPI debug tracebacks leak internals; `app/main.py:32`).
- **Verify:** curl without the header → 401; with header → 200; `/health` still
  open (Railway healthcheck passes).

### P0-4. POD economics are blind: hardcoded prices, no margin check — sales can LOSE money
- **Where:** `app/services/printify_client.py:110` (`price_cents: int = 2999`
  hardcoded for every variant of every product),
  `app/services/pipeline_orchestrator.py:927-932` (`estimated_price_range: "$10-25"`
  hardcoded for ALL formats), `app/agents/etsy/listing_generator.py:56-100`
  (LLM freely picks the Etsy price; fallback `price: None`).
- **What:** Nobody ever looks at Printify's actual production+shipping COST for
  the chosen blueprint/variant. The Etsy listing price is an LLM guess inside a
  hardcoded "$10-25" range — but a printed t-shirt's Printify base cost is
  typically $9-14 **plus shipping**, and Etsy takes ~6.5% transaction fee +
  payment fees + $0.20 listing fee. A $12 tee sale is a guaranteed loss. The
  hardcoded Printify `price` ($29.99) is irrelevant to what the buyer pays (the
  Etsy listing price is what matters) — margin is completely uncontrolled.
- **Fix:**
  1. In `PODFulfillmentService.create_product_for_task`, the
     `list_variants(blueprint_id, print_provider_id)` response already includes
     per-variant `cost` (Printify catalog variants carry cost data; if absent,
     fetch the created product readback — its `variants[].cost` is populated).
     Capture `max_variant_cost_cents`.
  2. Compute price: `etsy_price = ceil((max_cost + shipping_estimate + 0.20 + target_profit) / (1 - 0.10))`
     where 0.10 approximates Etsy fees (6.5% transaction + ~3% payment), and
     `target_profit` is a new setting `POD_TARGET_PROFIT_USD` (default e.g. 6.00).
  3. Pass that price into the listing instead of the LLM's guess for POD
     (`_stage_create_listing`: override `listing["price"]` when `is_pod`).
  4. Set the same value (in cents) as the Printify product `price` so the two
     systems agree.
  5. Store cost + price on `PODProduct` (new columns `cost_cents`, `price_cents`
     via a small startup migration in `app/db/migrations.py`) so margin per sale
     is auditable later.
- **Verify:** create a POD task; assert listing price ≥ variant cost + fees +
  target profit; log the numbers.

### P0-5. POD listings die after one sale, and buyers can't choose size — orders ship an arbitrary variant
- **Where:** `app/services/pipeline_orchestrator.py:938-943` (`quantity = 1` for
  POD listings), `app/workers/etsy_receipt_worker.py:246-247`
  (`variant_id = variant_ids[0]` — always the FIRST enabled variant),
  `app/services/pod_fulfillment_service.py:99-104` (10 variants picked but never
  exposed to the buyer).
- **What:** Two connected money bugs:
  1. **quantity=1**: after the first sale Etsy marks the listing sold out and
     deactivates it. Your winning product silently dies. Made-to-order goods
     should carry a high quantity (999).
  2. **No variations on the Etsy listing**: the buyer sees ONE price and no
     size/color picker. When a receipt arrives, the worker submits Printify
     variant `variant_ids[0]` — whatever variant happened to be first (often
     size S or a random color). A buyer expecting a Large gets whatever was
     first. That's refunds, bad reviews, and a dead shop.
- **Fix (staged):**
  1. Immediate (one line): `listing["quantity"] = 999` for POD in
     `_stage_create_listing`.
  2. Immediate honesty fix: restrict Printify product creation to ONE deliberate
     variant (e.g. unisex tee, size L, black/white) instead of 10 arbitrary ones
     (`pod_fulfillment_service.py:102` — pick by variant title match, not `[:10]`),
     and have the listing description state exactly what is sold ("Unisex tee,
     size L"). Then `variant_ids[0]` is genuinely what the buyer bought.
  3. Proper fix (bigger step): create Etsy listing **variations** (Etsy v3
     `updateListingInventory` endpoint: products[] with property_values for
     Size) mirroring the enabled Printify variants, then in the receipt worker
     map the transaction's `variations`/`product_id` to the matching Printify
     variant_id instead of `[0]`.
- **Verify:** test purchase flow (Etsy has no sandbox — verify by inspecting
  listing inventory JSON after creation, and unit-test the receipt→variant
  mapping with a fake receipt payload).

### P0-7. A failed fulfillment order is NEVER retried — the alert claims it will be
- **Where:** `app/workers/etsy_receipt_worker.py:124-151` (`_poll_new_receipts`
  advances `last_checked_at` unconditionally), alert text at lines 267-273 says
  "Will retry on next poll" — it won't.
- **What:** Receipts are fetched with `min_created=last_checked_at`. If
  `submit_order()` fails (Printify hiccup, bad address, rate limit), the
  exception is caught, an alert is sent… and then `last_checked_at` is advanced
  past the receipt's creation time. The next poll never sees that receipt again.
  **A paying customer gets nothing** unless you notice the Discord alert and act
  manually (there is also no manual resubmit endpoint).
  Related gap: `min_created` filters by receipt *creation* — a receipt created
  before the checkpoint but *paid* after it is missed entirely (`was_paid=true`
  only filters, it doesn't shift timestamps).
- **Fix:**
  1. Track failures: only advance `last_checked_at` to the timestamp of the
     oldest FAILED receipt (or don't advance at all if any failure occurred this
     poll). Simplest robust variant: keep a `failed_receipts` list in the state
     file `{receipt_id, first_seen, attempts}`; retry them each poll up to N
     attempts before giving up loudly.
  2. Use `min_last_modified` instead of `min_created` in the Etsy receipts query
     (Etsy supports it) so late-paid receipts appear.
  3. Idempotency already protects against double-submission
     ((receipt_id, transaction_id) unique) — retries are safe.
  4. Add `POST /pod/fulfillments/resubmit/{receipt_id}` for manual recovery.
  5. Fix the alert text either way.
- **Verify:** unit test `_poll_new_receipts` with a fulfillment service double
  that raises once then succeeds — the receipt must be processed on the second
  poll.

### P0-8. Real sales revenue is never recorded — the "learn what sells" loop runs on zero data
- **Where:** `app/services/revenue_service.py` (manual-only, stale docstring),
  `app/api/routes/analytics.py:52-69` (manual `POST /analytics/revenue`),
  `app/workers/etsy_receipt_worker.py:213-249` (digital receipts skipped with
  `continue`, POD receipts fulfilled but revenue not recorded).
- **What:** The receipt worker already polls paid receipts (transactions_r scope
  IS granted — the RevenueService docstring claiming otherwise is outdated).
  But nothing records the sale amount. So `PerformanceService` (50% of its score
  is revenue) and `BestProductsService` — the entire feedback loop that should
  steer the factory toward money-making products — always see $0 unless you
  manually POST every sale. Digital sales (your main format) are entirely
  invisible: the worker skips non-POD transactions.
- **Fix:**
  1. In `_process_receipt`, for EVERY transaction (digital included): resolve
     `task_id` — for POD via PODProduct; for digital via
     `ImageAsset.listing_id == transaction.listing_id` (the catalog already
     persists listing_id on publish — `ImageCatalogService`).
  2. Record `RevenueService.record_sale(task_id, amount=transaction price, quantity=...)`.
     Transaction price: Etsy ShopTransaction has `price: {amount, divisor}` —
     `amount/divisor * quantity`. Add idempotency: skip if an analytics event
     `sale_recorded` with payload `transaction_id` already exists (add
     `transaction_id` to the payload).
  3. Update the stale docstrings in `revenue_service.py` and
     `analytics.py::record_sale`.
- **Verify:** feed a fake paid receipt through `_process_receipt` (unit test) →
  one `sale_recorded` event per transaction with the right amount; run again →
  no duplicates.

### P0-9. Pipeline is not crash-resumable: a restart mid-pipeline silently loses the product (money already spent)
- **Where:** `app/services/task_processor.py:80-83` (pipeline fires once, inline,
  after DONE), `app/services/task_service.py:250-290` (`recover_orphaned_tasks`
  only rescues PLANNED/RUNNING/QA), `app/services/task_queue.py` (in-memory).
- **What:** Three gaps:
  1. The post-completion pipeline (image gen → listing) takes minutes. If
     Railway restarts/redeploys mid-pipeline, the task is already DONE — on
     startup nothing re-runs the pipeline. Money spent on LLM + any images
     already generated; no listing; no alert; the task looks "done" forever.
  2. Tasks still in **NEW** at crash time are stranded: the queue is in-memory
     and startup recovery doesn't scan NEW (the docstring in task_queue.py
     points to `run_pending()`, but nothing calls it on startup).
  3. There is no manual endpoint to (re)run the pipeline for a DONE task.
- **Fix:**
  1. Startup: in `app/main.py::startup_event`, after `recover_orphaned_tasks()`,
     enqueue all NEW tasks (`SELECT id FROM tasks WHERE status='NEW'` →
     `queue.enqueue`).
  2. Persist a pipeline outcome marker: `run_post_completion` already writes
     `output_data.pipeline_status` on block — also write
     `pipeline_status: "COMPLETED"` + `listing_id` on success (in
     `_stage_attach_publish` success branch via `task_service`).
  3. Startup scan: DONE tasks with a recognized product format and NO
     `pipeline_status` → re-run `run_post_completion` (it is largely idempotent:
     catalog register is idempotent on path, marketing stages check for prior
     success; images regenerate — acceptable, or check for existing files first).
  4. Add `POST /tasks/{task_id}/pipeline` calling `run_post_completion` for
     manual re-runs.
- **Verify:** create task, kill server after delivery-asset stage, restart →
  pipeline resumes and produces a listing; NEW task before restart gets processed.

### P0-10. Etsy OAuth refresh race can take the whole shop integration down
- **Where:** `app/services/etsy_oauth.py:95-122` (`get_valid_access_token`).
- **What:** Four worker threads + API requests all call this. When the token is
  near expiry, several threads refresh concurrently. Etsy rotates refresh
  tokens on every refresh — if thread A and B both send the OLD refresh token,
  whichever lands second gets rejected AND may invalidate the family; worse, if
  A's new token is saved and then B's older response overwrites the row, the
  stored refresh token is dead. Result: 401s everywhere — no listings, no
  fulfillment, no receipts — until you manually re-run the OAuth flow. This is
  a time bomb for an unattended money system.
- **Fix:**
  1. Module-level `threading.Lock()`; take it around the whole
     expiry-check-and-refresh sequence; after acquiring, RE-READ the token row
     (another thread may have just refreshed) before deciding to refresh.
  2. (The same pattern applies to `tumblr_oauth.get_valid_access_token` /
     `pinterest_oauth` — same single-row refresh design.)
  3. Log refresh events (`LogService.info`) so a failure is diagnosable.
- **Verify:** unit test with a fake httpx transport: 10 threads call
  `get_valid_access_token` with an expired token → exactly ONE refresh HTTP call.

### P0-11. Listing price can be None/0 and digital prices are ungrounded guesses
- **Where:** `app/agents/etsy/listing_generator.py:93-100` (fallback
  `price: None`), `app/services/etsy_client.py:43` (`"price": listing.get("price") or 0`),
  `app/services/pipeline_orchestrator.py:930` (every format told "$10-25").
- **What:** If the pricing LLM call returns bad JSON twice, price becomes None →
  EtsyClient sends `0` → Etsy rejects (min $0.20) and listing creation fails, or
  worse silently produces a nonsense price. Separately, ALL formats share the
  hardcoded "$10-25" hint: typical Etsy prices are ~$2-6 for a single coloring
  page/wallpaper, ~$5-15 for planners — overpricing digital kills conversion,
  and POD needs cost-based pricing (P0-4).
- **Fix:**
  1. Add per-format price bands to `PRODUCT_FORMATS`
     (`app/core/product_formats.py`), e.g.
     `single_print: (3.50, 8.00)`, `coloring_page: (2.00, 4.50)`,
     `phone_wallpaper: (2.00, 4.00)`, `greeting_card_design: (3.00, 6.00)`,
     `sticker_sheet_design: (3.00, 6.00)`, `pdf_planner_or_guide: (5.00, 12.00)`,
     `pod_apparel_design: computed per P0-4`.
  2. In `_stage_create_listing`, pass the band into the product dict as
     `estimated_price_range`, and after `generate_listing()` CLAMP:
     `if not isinstance(price, (int,float)) or price < lo or price > hi: price = midpoint`.
  3. Never send 0: raise/block instead if price is still invalid.
- **Verify:** unit test with a stub LLM returning garbage → final listing price
  is the band midpoint, never 0/None.

### P0-12. Printify blueprint selection is fed a meaningless string — the physical product is essentially random
- **Where:** `app/services/pod_fulfillment_service.py:89-91`
  (`concept = f"task_id={task_id}"`), `:87` (`blueprints_raw[:80]` — first 80 of
  ~1000+ blueprints, ordered arbitrarily by Printify).
- **What:** `ProductTypeSelectorAgent.select(concept, blueprints)` is asked to
  pick the best product for the design — but "the design" is described only as
  `task_id=e881c422…`. The LLM cannot do anything sensible with that; whatever
  it picks, the buyer-facing product (tee? mug? poster?) is disconnected from
  the design/trend intent. Also only the first 80 blueprints are even shown.
- **Fix:**
  1. `create_product_for_task` should accept (or look up via `TaskService`) the
     task's `output_data.title` + `description` and pass a real concept string.
     The orchestrator has `product_name`/`visual_brief` in scope at the call
     site (`_stage_printify_precheck`) — pass them through.
  2. Replace `[:80]` with a curated allow-list of blueprint IDs suited to
     `pod_apparel_design` (e.g. the standard Bella+Canvas 3001 tee, a hoodie, a
     mug) in a module constant, and let the selector choose among those. A
     curated list also keeps taxonomy_id=482 (T-shirts) honest — right now the
     listing is always categorized as a T-shirt even if the selector picked a
     mug blueprint.
- **Verify:** log the selection input/output; create a "funny cat coloring tee"
  task → selector receives the real concept and picks from the curated list.

### P0-13. Daily spend accounting is fiction — caps can't protect you
- **Where:** `app/workers/autonomy_worker.py:97-107` (flat $0.30 estimate,
  records $0.05), `app/services/pipeline_orchestrator.py:736-745` (records flat
  $0.20 for "images"), nothing records: PDF pages (up to 6 × $0.04), content-QA
  vision calls (1-2+ per asset), consistency checks + up to 2 remakes (each
  remake = 1 image + 2 vision calls), mockup scene generations (2 × $0.04 per
  digital product), Pinterest pin ($0.04), per-page QA retries. Etsy's $0.20
  listing fee (real money on publish) is not tracked anywhere either.
- **What:** A pdf_planner_or_guide task's true cost is roughly $0.40-0.80+, not
  $0.25. With MAX_TASKS_PER_DAY=10 the real ceiling is ~3x what the
  $5 MAX_DAILY_SPEND_USD ledger believes. The caps "work", but the numbers they
  enforce are wrong — you can overspend while the ledger says you're fine.
- **Fix (pragmatic, no full metering rewrite):**
  1. Centralize image-spend recording at the choke point: every image request
     goes through `OpenRouterImageProvider.generate_image`. Wrap it: after a
     successful call, `AutonomyService().record_spend(0.04, f"image {caller}")`.
     Seedream is flat-rate per image, so count × $0.04 is accurate. (Guard:
     only when the task is autonomy-sourced if you want to keep manual tasks
     out of the cap — simplest is to record ALL image spend; the cap then
     protects your wallet globally, which is what you actually want.)
  2. Record vision-QA calls at ~$0.002 each in `ContentQualityService` the same
     way (cheap, but makes the ledger honest).
  3. Remove the flat $0.20/$0.05 records to avoid double counting; keep a small
     upfront reservation check (`can_spend(0.80)`) before starting a cycle.
- **Verify:** run one full pdf task; `GET /dashboard` autonomy state shows a
  spend within a few cents of the OpenRouter dashboard's delta.

---

## TIER P1 — Product-quality issues that directly hurt conversion/refunds

### P1-1. POD listing photos are AI-imagined products, not the real Printify mockups
- **Where:** `app/services/pipeline_orchestrator.py:131-138` (POD keeps
  independent `_stage_listing_images` generation; the comment even claims "its
  listing photos are real product mockups from Printify" — they are NOT),
  `product_image_agent.py:115-134` (generic "product photography" prompts).
- **What:** For `pod_apparel_design`, hero/lifestyle are text-to-image guesses
  of what a shirt with that design might look like. The consistency gate
  compares them against the FLAT design and often lets plausible-but-wrong
  garment/color/placement through. Buyers judge a POD product by the mockup —
  mismatch = returns/complaints. Meanwhile Printify **generates real mockup
  images for every created product** (`GET product` response `images[]` array).
- **Fix:** after `_stage_printify_precheck` succeeds, fetch
  `PrintifyClient.get_product(...)['images']`, download 2-3 mockup URLs
  (front/back/lifestyle), validate via `ImageValidationService`, and use those
  as `image_paths` for POD instead of `_stage_listing_images` output (skip the
  generation entirely — also saves 2 image calls per POD product). Fix the
  misleading comment.
- **Verify:** POD task → listing photos on Etsy are the actual Printify mockup
  renders of the uploaded design.

### P1-2. Phone wallpapers (and greeting cards) are delivered as 1:1 squares
- **Where:** `app/agents/image/pod_design_agent.py:76-77` (delivery always
  `aspect_ratio="1:1"`), `app/services/image_validation_service.py:42-47`
  (delivery rules hardcode square 1:1), `app/core/product_formats.py` (no
  aspect metadata per format).
- **What:** A `phone_wallpaper` product delivered as a 1024/2048 square is
  simply a bad product — phones are 9:16/9:19.5. Buyers will 1-star it.
  Greeting cards are typically 5:7/A5 portrait. Only `single_print`,
  `sticker_sheet_design`, `coloring_page` are fine-ish as squares (even
  coloring pages are usually 8.5×11 portrait).
- **Fix:**
  1. Add `"delivery_aspect"` to each `PRODUCT_FORMATS` entry:
     `phone_wallpaper: "9:16"`, `greeting_card_design: "3:4"` (nearest supported),
     `coloring_page: "3:4"`, others `"1:1"`. (Supported ratios per provider
     docstring: 1:1, 2:3, 3:2, 4:3, 3:4, 16:9, 9:16, …)
  2. Thread it: `_stage_pod_design` → `PODPipelineService` →
     `PODDesignAgent.generate_design(aspect_ratio=...)`. Watch the Seedream
     pixel-floor: 9:16 at 2K may be under the 3,686,400-pixel minimum — use 4K
     for non-square (same fix as PDF pages, see `pdf_generation_service.py:44-53`).
  3. Extend `USE_CASE_RULES` to accept an expected ratio parameter (pass the
     format's ratio into `validate(path, use_case="delivery", expected_ratio=...)`)
     instead of hardcoding 1:1.
  4. Mockup compositing (`MockupService`) already uses `ImageOps.contain`, so
     non-square designs composite fine.
- **Verify:** create a phone_wallpaper task → delivered PNG is 9:16 and passes
  validation; listing mockups render correctly.

### P1-3. Every delivered PDF page has the raw page brief stamped in tiny text on it
- **Where:** `app/services/pdf_generation_service.py:238-249` (`_with_caption`
  draws `brief[:80]` bottom-left with `ImageFont.load_default()` on the final
  page image), called at line 142 BEFORE QA.
- **What:** The buyer's PDF pages each carry a stray machine-looking caption
  (e.g. "Coffee Purchase Tracker") in a ~10px default font at 4K resolution —
  microscopic, misaligned, unprofessional. It also directly contradicts the
  page prompt's own "no meta-text" rule, and the strict per-page QA reviewer is
  told to reject stray meta-text — the caption survives only because it's drawn
  AFTER generation and the reviewer usually can't see 10px text on a downscaled
  1024px review image. This is a defect in the actual paid deliverable.
- **Fix:** delete the `_with_caption` call (line 142) and the method, OR render
  a real header intentionally (large font via `ImageFont.truetype`, centered,
  only when the page genuinely lacks a heading — not recommended; the generated
  layout already contains headings). Simplest correct action: remove it.
- **Verify:** generate a PDF; extract pages (`pypdf`) → no stamped caption text.

### P1-4. PDF per-page QA fails OPEN; single-image QA fails CLOSED — outage ships unreviewed pages
- **Where:** `app/services/pdf_generation_service.py:200-227` (`_review_page`
  returns None on exception → `if qa is None or qa.passed:` treats it as pass);
  contrast `pipeline_orchestrator.py:302-330` (single-image QA exception →
  blocked).
- **What:** If the vision model errors (rate limit, key issue) during PDF
  generation, every page silently passes content QA and a possibly-garbled PDF
  ships toward a paying customer. Inconsistent with the rest of the gate design.
- **Fix:** in `generate_pdf`'s loop, treat a raised/None review as a failed
  attempt (retry), and after `qa_attempts` raise `PDFGenerationError` ("page QA
  unavailable") so the orchestrator blocks the task like any other gate failure.
  Keep the test-double path working by making the injected double return a
  passing result rather than relying on None-passes (update
  `scripts/test_step100l_pdf_page_qa.py` accordingly if needed).
- **Verify:** unit test with a QA double that raises → `PDFGenerationError`,
  task blocked; existing 100l tests still pass.

### P1-5. Assembled PDF size is never checked against Etsy's 20MB digital-file limit
- **Where:** `app/services/pdf_generation_service.py:251-255`
  (`_assemble_pdf_bytes`), pages generated at 4K (2732×4096) per page.
- **What:** Six 11-megapixel pages can exceed Etsy's 20MB-per-file cap; the
  upload then 4xxs, the listing is deleted and the task blocked — after paying
  for all 6 page generations + QA. Even under 20MB, buyers download bloated
  files.
- **Fix:** in `_assemble_pdf_bytes`, downscale each page to max ~2200px on the
  long edge (≈ 260dpi letter — still print-quality) and save with
  `resolution=150.0`; after assembly, if `len(pdf_bytes) > 19_000_000`, re-encode
  pages as JPEG quality 85 inside the PDF (`img.convert("RGB")` + save with
  Pillow's PDF JPEG path) and re-check; raise `PDFGenerationError` if still over.
- **Verify:** generate a 6-page PDF → file < 20MB, pages readable at print size.

### P1-6. Listing description can promise a different page count than the PDF actually has
- **Where:** `app/services/pipeline_orchestrator.py:864-885`
  (`_resolve_pdf_page_briefs`: pages = `output_data.sections` truncated to 6),
  `app/core/schemas/seo_schema.py:9` (sections min 4), trend agent's
  `page_count` only used when sections are absent.
- **What:** The trend concept says e.g. `page_count: 5`; SEO generation
  independently produces ≥4 `sections`; the PDF gets `len(sections)` pages
  (4-6). If the LLM description text mentions "5-page planner" (it often echoes
  the concept), the buyer receives a different count → refund territory.
- **Fix:** after `_resolve_pdf_page_briefs`, reconcile: pass the FINAL page
  count into the listing description stage — simplest: append a truthful line
  to the description in `_stage_create_listing` for pdf formats
  (`f"\n\nIncludes {n} printable pages."`) and strip/avoid page-count claims in
  the SEO prompt (add "do not state a page count" to the executor prompt for
  pdf tasks, or post-process description with a regex replacing "N-page/N pages"
  with the real count).
- **Verify:** create a pdf task where sections=4 but metadata page_count=6 →
  published description says 4 pages.

### P1-7. Shipping profile pick doesn't do what its comment says — can attach a digital/wrong profile to POD listings
- **Where:** `app/services/etsy_shipping_service.py:84-89` (comment: "Skip
  digital-only profiles" — code takes the FIRST non-deleted profile of any type),
  `_create_default` ($5 primary / $2 secondary flat, everywhere).
- **What:** If the shop has a digital or otherwise-unsuitable profile first in
  the list, POD listings get it → checkout shipping is wrong (either free-ships
  a $6-a-piece shipment eating margin, or blocks buyers). The $5 flat default
  also ignores Printify's real shipping (which varies by provider/region).
- **Fix:** filter profiles: Etsy profile objects carry `type`
  (`manual`/`calculated`) and entries with `origin_country_iso`; require a
  profile whose `min_processing_time` exists and which is NOT the digital
  auto-profile (digital listings' profiles have `type == "digital"` in v3 —
  check the real field on your shop's payload and filter on it; log the chosen
  profile). Set `ETSY_SHIPPING_PROFILE_ID` in Railway once and this whole path
  is skipped — do that as the immediate mitigation, and document the value in
  the changelog. For pricing: either set shipping cost from Printify's shipping
  API (`GET /v1/shops/{shop}/orders/shipping.json` estimate) or fold shipping
  into item price with free shipping (Etsy boosts free-shipping listings ≥$35;
  for single tees flat $5 is acceptable if P0-4's margin math includes actual
  Printify shipping).
- **Verify:** log output of `get_or_create()`; assert the profile used on a new
  POD listing matches the intended physical profile in Etsy Shop Manager.

### P1-8. Printify product gets a junk internal title
- **Where:** `app/services/pod_fulfillment_service.py:107`
  (`title = f"AI Factory Product — task {task_id[:8]}"`).
- **What:** Mostly cosmetic today (orders are placed via API), but it makes the
  Printify dashboard unmanageable at scale and would leak an ugly name if the
  product is ever published to a channel.
- **Fix:** pass the real product title through (same plumbing as P0-12 gives
  you `product_name` at the call site).

### P1-9. SEO title is capped at 70 chars — Etsy gives you 140
- **Where:** `app/core/schemas/seo_schema.py:6` (max_length=70).
- **What:** Etsy search weights the title heavily; sellers use most of 140
  chars for long-tail keywords. Capping at 70 halves the discoverability
  surface — this is a conversion/discovery lever, i.e. money.
- **Fix:** raise `max_length` to 140 and update the executor prompt
  (`app/core/agents/executor.py`) to ask for "an Etsy-optimized title of
  120-140 characters, front-loading the main keyword, separated by commas or
  pipes". Keep min 20. (The pipeline already truncates defensively at 140:
  `etsy_client.py:41`.)
- **Verify:** new listings carry ~130-char keyword-rich titles.

### P1-10. Trend seed keywords research products the pipeline cannot build
- **Where:** `app/services/trend_data_service.py:23-32` (`SEED_KEYWORDS`
  includes "svg files", "clipart bundle", "wedding invitation template").
- **What:** Google Trends anchors steer the research agent toward SVGs, clipart
  BUNDLES (explicitly banned by the multi-item validator!) and editable
  templates — none of which the image pipeline can deliver. Cycles then burn
  concept-generation retries or produce mismatched products.
- **Fix:** replace with format-aligned seeds: "printable wall art",
  "digital planner", "coloring pages", "phone wallpaper aesthetic",
  "sticker sheet", "greeting card printable", "funny t shirt", "budget planner
  printable". These can be tuned via `TREND_SEED_KEYWORDS` env without a deploy
  (already supported) — set it in Railway now, and change the code default in
  the same step.

---

## TIER P2 — Technical robustness / reliability

### P2-1. SQLite is shared by 5+ threads with no WAL or busy timeout
- **Where:** `app/db/database.py:18` (`check_same_thread=False` only).
- **What:** TaskWorker, EtsyReceiptWorker, AutonomyWorker,
  MarketingRefreshWorker and API request threads all write. Default SQLite
  journal mode + no `busy_timeout` → intermittent
  `sqlite3.OperationalError: database is locked` under overlap (e.g. receipt
  poll during a pipeline run). Today's traffic makes it rare — it will bite
  exactly when sales (writes) pick up.
- **Fix:** after `create_engine`, register a connect listener:
  ```python
  from sqlalchemy import event
  @event.listens_for(engine, "connect")
  def _set_sqlite_pragma(dbapi_conn, _):
      cur = dbapi_conn.cursor()
      cur.execute("PRAGMA journal_mode=WAL")
      cur.execute("PRAGMA busy_timeout=5000")
      cur.close()
  ```
- **Verify:** run a task while hammering `GET /tasks` and the dashboard — no
  locked errors; `PRAGMA journal_mode` returns `wal`.

### P2-2. Multi-step execution burns LLM calls whose output is thrown away
- **Where:** `app/services/task_processor.py:130-152` (each plan step generates
  a FULL SEO JSON; outputs joined with `\n\n`),
  `app/core/utils/json_sanitizer.py:35-70` (extracts the FIRST balanced JSON
  object → only step 1's output ever becomes `output_data`).
- **What:** PlannerAgent typically returns 3 steps; ExecutorAgent runs 3 full
  generations; QA keeps only the first JSON blob. Steps 2-3 are pure wasted
  spend AND the growing `context` makes later calls more expensive. (Confirmed
  by reading the sanitizer's first-object scan.)
- **Fix:** for product-format tasks, skip planning multi-steps: either make
  `PlannerAgent.create_plan` return a single consolidated step for recognized
  product formats, or in `_execute` run only the LAST/first step for
  `task.type in PRODUCT_FORMATS`. One LLM call per product is enough — the
  executor prompt already asks for the complete SEO object.
- **Verify:** task logs show exactly one ExecutorAgent generation per product
  task; output_data unchanged in shape.

### P2-3. `/tasks/etsy/listing` endpoint creates tasks the pipeline deliberately ignores
- **Where:** `app/api/routes/tasks.py:68-71` (hardcodes `type="seo_writing"`),
  `pipeline_orchestrator.py:100-105` (default-deny skips it).
- **What:** Legacy trap: calling it looks successful but can never produce a
  listing. Either remove it or make it take a `product_format` parameter
  validated against `PRODUCT_FORMATS`.
- **Fix:** delete the endpoint (preferred — `POST /tasks` with a proper `type`
  covers it), or require `request.product_format` and pass it as `type`.

### P2-4. Worker health monitoring misses MarketingRefreshWorker and watches for log sources that don't exist
- **Where:** `app/api/routes/health.py:9-13` (`_WORKER_MAX_AGE` lacks
  MarketingRefreshWorker — `/health/workers` never reports it);
  `app/api/routes/dashboard.py` room `events` filters reference sources like
  `"EtsyService"`, `"SEOAgent"`, `"QAValidator"`, `"ListingAgent"`,
  `"TaskService"`, `"PlannerAgent"` — several never appear as `source` values in
  real logs (grep LogService callers: sources are e.g. `TaskProcessor`,
  `PipelineOrchestrator`, `ProductImageAgent`, `ExecutorAgent`, worker names).
  So most rooms show no errors even when their stage is failing.
- **Fix:** add `"MarketingRefreshWorker": 43200` to `_WORKER_MAX_AGE`; align the
  dashboard `_errors_for` source sets with actual sources
  (`PipelineOrchestrator` errors should surface in storefront/marketing rooms —
  today they appear nowhere).

### P2-5. Fulfillment tracking only looks at the first shipment
- **Where:** `app/services/pod_fulfillment_service.py:303` (`shipments[0]`).
- **What:** Multi-item orders can ship in multiple parcels; only the first
  tracking number reaches Etsy. Minor today (single-variant orders), but log it.
- **Fix:** push tracking for each shipment (Etsy accepts multiple tracking
  posts per receipt), or at least the latest.

### P2-6. Buyer email/phone not forwarded to Printify orders
- **Where:** `app/workers/etsy_receipt_worker.py:197-208` (`"email": ""`),
  Etsy ShopReceipt provides `buyer_email`.
- **What:** Some print providers/carriers require contact info for delivery
  problems/customs; empty strings risk order rejection or failed delivery in
  some regions.
- **Fix:** `"email": receipt.get("buyer_email") or ""`; map phone if present.

### P2-7. Receipt polling has no pagination
- **Where:** `etsy_receipt_worker.py:161` (`limit: 100`, no offset loop).
- **What:** >100 new receipts in one window (a viral day, or first poll after
  long downtime) silently drops the tail. Low probability, high cost when it
  happens (unfulfilled orders).
- **Fix:** loop with `offset` until `results` count < limit.

### P2-8. `_extract_pdf_pages` leaks temp files
- **Where:** `pipeline_orchestrator.py:675-698` (NamedTemporaryFile
  delete=False, never removed).
- **Fix:** write into `data/images/listing/{task_id}/page{i}.png` instead (they
  are legitimately useful assets), or `finally: unlink` after mockups are built.

### P2-9. Alert debounce can swallow DIFFERENT failures sharing a title
- **Where:** `app/services/alert_service.py:59-67` (debounce keyed on title
  only, 60s).
- **What:** e.g. two different tasks blocked within a minute → one alert. Fine
  for storms, but you may under-count failures.
- **Fix:** debounce on `title + first 50 chars of message`, or include a count
  in a follow-up alert. Low priority.

### P2-10. Local venv is out of sync with requirements.txt — PDF + trends can't run/test locally
- **Where:** local `venv` (this machine): `pypdf`, `pytrends`, `pandas` missing
  (confirmed: `import pypdf` fails; `scripts/test_step100k_pdf_mockups.py` and
  step100l test [4] fail only for this reason).
- **Fix:** `./venv/Scripts/python.exe -m pip install -r requirements.txt`, then
  re-run `scripts/test_step100k_pdf_mockups.py` and `test_step100l_pdf_page_qa.py`
  — all should pass. Do this before any local pipeline testing.

### P2-11. Unpinned dependencies risk a broken deploy
- **Where:** `requirements.txt:10-11` (`openai`, `Pillow` unpinned; `pandas`
  arrives transitively via pytrends unpinned).
- **What:** A Railway rebuild can silently pull a breaking major (openai v2.x
  changes, Pillow API removals). For an unattended money system, pin everything.
- **Fix:** pin to the currently-deployed versions (`pip freeze` on Railway or
  local after P2-10) — e.g. `openai==<current>`, `Pillow==12.3.0`,
  `pandas==<current>`.

### P2-12. `groq` dependency + provider are dead code
- **Where:** `requirements.txt:4`, `app/core/providers/groq_provider.py`,
  `huggingface.py`, `dalle3_provider.py` — nothing instantiates them
  (`ProviderManager` is OpenRouter-only).
- **Fix:** delete the unused providers + requirement (keeps the deploy small
  and the audit surface honest). Low priority.

---

## TIER P3 — Hygiene, docs, small stuff (log everything, even small)

### P3-1. Git hygiene: live DB, bytecode and test images are committed
- **What:**
  - `app.db` is TRACKED and currently modified (committed in `75fd919 "quality
    test"`); `.gitignore`'s `*.db` doesn't untrack it. Committing a live SQLite
    file guarantees perpetual dirty status and merge pain, and can leak tokens
    (EtsyToken/TumblrToken rows live in it!). **Security-relevant.**
  - `app/core/agents/__pycache__/*.pyc` and `app/core/providers/__pycache__/*.pyc`
    tracked (also currently "modified").
  - `data/images/listing/test-*/*.png` test artifacts tracked;
    `data/images/delivery/` currently untracked-dirty; `data/receipt_worker_state.json`
    tracked (runtime state).
- **Fix:**
  ```
  git rm --cached app.db data/receipt_worker_state.json
  git rm -r --cached app/core/agents/__pycache__ app/core/providers/__pycache__
  git rm -r --cached data/images
  echo "data/" >> .gitignore   # runtime artifacts; images are runtime output
  git commit -m "Stop tracking runtime artifacts (db, pycache, generated images)"
  ```
  Then rotate the Etsy/Tumblr tokens if the repo has ever been pushed anywhere
  shared (the committed app.db contains them).

### P3-2. `DEBUG=True`, `ENV=development` defaults in production
- **Where:** `config/settings.py:11-12`, used at `app/main.py:32`.
- **Fix:** set `ENV=production`, `DEBUG=False` in Railway env. (Also part of
  P0-3.)

### P3-3. Stale/contradictory docs and comments (doc rot that will mislead future fixes)
- `revenue_service.py` docstring: claims no transactions scope / no receipts
  API — false since step 81 (P0-8 fixes the code; fix the text too).
- `pipeline_orchestrator.py:129-130`: claims POD listing photos are "real
  product mockups from Printify" — they aren't (P1-1).
- `pod_design_agent.py` module docstring still describes DALL-E 3.
- `openrouter_image_provider.py:12-13` says default model is
  `google/gemini-3.1-flash-image`; the actual settings default is
  `bytedance-seed/seedream-4.5`.
- `base_agent.py:1-9`: duplicated import block.
- `task_processor.py:22-26`: "placeholder work" comment is obsolete.
- `image_file_service.py` docstring: "Smaller or watermarked later" — the
  current mockups are perspective-composited, not watermarked; and
  `mockup_service.py:43` says "watermarked" in the class docstring while the
  module docstring says "no watermark needed". Pick one term (perspective
  previews) everywhere.

### P3-4. `EtsyImageService.upload_listing_image` hardcodes `image/png`
- **Where:** `etsy_image_service.py:113`.
- **What:** Fine while everything is PNG; will silently mislabel if JPEG
  mockups are ever produced. Use `_guess_content_type` (already exists in the
  same file).

### P3-5. Workers alert on death but never self-heal
- **Where:** all four workers' `finally` blocks; `_check_worker_health` only
  alerts.
- **What:** A dead thread (unhandled non-Exception like MemoryError, or a bug in
  the loop guard) leaves the process healthy-looking (uvicorn still serves) but
  the factory stopped. Railway restart policy only triggers on process crash.
- **Fix:** simplest: in `_check_worker_health`, when a heartbeat is stale AND
  its thread is not alive, call `worker.start()` again (keep a registry of
  worker instances in `app/main.py`), or make `/health/workers` return
  HTTP 503 when degraded and point the Railway healthcheck at it so the
  platform restarts the whole service.

### P3-6. Mockup scene generation re-buys the same two background scenes for every product
- **Where:** `app/services/mockup_service.py:191-209` (`_scene` generates a
  fresh empty wall/desk per product; scenes are product-agnostic).
- **What:** 2 × $0.04 per digital product for backgrounds that could be reused.
- **Fix:** cache generated scenes to `data/images/scenes/{role}_{n}.png` (keep
  e.g. 5 per role, pick randomly for variety); regenerate only when the cache
  is empty. Saves ~$0.08/product with zero quality loss.

### P3-7. Test suite hygiene
- `tests/` directory is EMPTY — all tests are ad-hoc scripts in `scripts/`,
  not runnable via pytest, with no CI. Migrate the deterministic ones
  (100b/d/f/g/i/j/k/l, sanitizer, seo_schema) into `tests/` as pytest tests so
  regressions are caught mechanically.
- `scripts/test_sanitizer.py` is broken: missing the
  `sys.path.insert(0, ...)` bootstrap every other script has → ModuleNotFoundError.
- `scripts/test_state_machine.py`: (a) crashes on Windows consoles (cp1250
  can't print "✓" — add `sys.stdout.reconfigure(encoding="utf-8")` or use
  ASCII), and (b) creates tasks in the REAL configured DB — point it at a temp
  SQLite via `DATABASE_PATH` env before importing app modules.

### P3-8. Misc small correctness notes
- `app/services/task_service.py:105-122` `save_plan` stores the plan into
  `task.metadata_`, OVERWRITING the autonomy metadata
  (`source: autonomy_worker`, `page_count`) — after planning,
  `is_autonomy` detection at `pipeline_orchestrator.py:114` reads
  `task.metadata_.get("source")` which the planner just clobbered →
  **autonomy image spend is likely never recorded** (compounds P0-13) and
  `page_count` metadata is lost (masked because sections usually exist).
  Fix: merge instead of replace in `save_plan` (`merged = {**(task.metadata_ or {}), "plan": plan}`)
  and read the plan from `metadata_["plan"]` in `_execute`. **Verify** by
  checking a real autonomy task's row: `metadata` currently holds only the plan.
  (This one is genuinely a bug — treat as P1 if you rely on autonomy spend
  records.)
- `app/services/etsy_oauth.py:44`: manual query-string assembly is fragile;
  use `str(httpx.QueryParams(params))` like tumblr_oauth does.
- `PerformanceService._marketing_score` fetches only the latest 1000 events
  and filters in Python — fine now, note it degrades as events grow; add a
  payload-based filter/index if analytics grows.
- `dashboard.py:93`: mixes `datetime.now(timezone.utc)` and naive
  `datetime.utcnow()` cutoffs — consistent-enough, but standardize on aware UTC
  when touched next.
- `orchestrator/core.py` `run_pending` processes tasks synchronously inside an
  HTTP request — long-running request, may hit platform timeouts. Prefer
  enqueueing NEW tasks onto TaskQueue (which P0-9's startup fix needs anyway).
- `settings.TREND_SEED_KEYWORDS: list = []` — pydantic-settings parses env
  lists as JSON; document `TREND_SEED_KEYWORDS='["printable wall art", ...]'`
  format in the changelog when you set it (a plain comma string will fail).
- `check_token.py`, `designsforall.txt`, `taxonomy_output.txt` sit in repo
  root — move to `scripts/`/`instructions/` or delete.

---

## Suggested working order (each is a self-contained step)

1. **P0-3** API auth + DEBUG off (protects the wallet before anything else) — small.
2. **P0-6** Pinterest guard (stops per-task waste) — tiny.
3. **P0-7** receipt retry + `min_last_modified` (protects real orders) — medium.
4. **P0-10** OAuth refresh lock — small.
5. **P0-1 + P0-2** review→publish endpoint + marketing gated on published — medium. **This is the step that turns the factory on.**
6. **P0-8** auto revenue recording — small/medium.
7. **P0-11 + P0-4 + P0-5** pricing floors, POD margin math, quantity/variants — medium/large.
8. **P0-9** pipeline resumability — medium.
9. **P0-12, P0-13**, then P1 quality items (P1-2 wallpaper aspect and P1-3 PDF
   caption first — they affect the actual paid deliverable), then P2/P3.
10. **P3-8 save_plan metadata clobber** alongside P0-13 (same subsystem).

---

## Appendix A — test evidence from this audit

- `venv/Scripts/python.exe -m compileall app config` → exit 0 (no syntax errors).
- Passing suites (offline, deterministic): `test_step100b_consistency_remake`
  8/8, `test_step100d_coloring_page_prompts` 5/5, `test_step100f_pdf_planner_prompts`
  4/4, `test_step100g_delivery_mockups` 6/6, `test_step100i_tumblr_on_listing`
  4/4, `test_step100j_white_background` 3/3, `test_seo_schema` all cases pass.
- `test_step100k_pdf_mockups`: **ModuleNotFoundError: pypdf** (local venv only —
  P2-10).
- `test_step100l_pdf_page_qa`: 3/4 — case [4] fails because `generate_pdf`
  raises on the same missing `pypdf` during readback (P2-10), not a code bug.
- `test_sanitizer.py`: ModuleNotFoundError (missing sys.path bootstrap — P3-7).
- `test_state_machine.py`: UnicodeEncodeError on cp1250 console + writes to real
  DB (P3-7).
- Manual verification: sanitizer first-JSON-object behavior (P2-2) confirmed by
  reading `json_sanitizer.py`; no auth middleware confirmed by grep over
  `app/api` + `app/main.py`; `app.db` tracked confirmed via `git ls-files`;
  Pinterest stage pre-token image generation confirmed by call order in
  `_stage_pinterest` → `enrich_listing_with_image` → `SocialImageAgent`.
