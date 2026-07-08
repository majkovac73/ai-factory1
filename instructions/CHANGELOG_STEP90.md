
## Step 90 — Hard product-before-listing gate + specific concept generation
**Date:** 2026-07-08

---

### Root causes

A real Etsy draft listing went out reading as "a collection of digital
products and print-on-demand products" — a description of the whole
business, not a specific item — with no real deliverable file behind it.
Two root causes, both fixed:

1. `PipelineOrchestrator` did not treat "a real product artifact exists and
   passes quality checks" as a hard blocking requirement before Etsy listing
   creation. It logged image/design failures but still proceeded to create a
   listing regardless.
2. `TrendResearchAgent` passed `IntelligenceAgent`'s vague market-strategy
   sentence (e.g. "Focus on niche markets catering to specific hobbies or
   demographics") straight through as if it were a specific product concept.
   Nothing converted the broad insight into one concrete, nameable, sellable
   item before the rest of the pipeline ran. Compounding this, `AutonomyWorker`
   always created tasks with `type="general"` — which is neither `"pod"` nor
   `"digital_download"` — so the delivery-asset generation stage never even
   ran for autonomy-created tasks.

---

### Fix 1 — Hard gate: no listing without a verified real product

**`app/services/pipeline_orchestrator.py`** — restructured stage order so the
gate runs *before* `create_draft_listing()` is ever called:

1. `listing_images` (always)
2. `pod_design` — delivery asset generation, for `pod`/`digital_download` only
3. `printify_precheck` (POD only) — `PODFulfillmentService.create_product_for_task()`
   now runs *before* a listing exists (no `etsy_listing_id` yet), so its
   failure can block listing creation outright
4. **HARD GATE** — `ImageCatalogService.get_delivery_asset(task_id)` must
   return a real, validated asset (validation already happens before catalog
   registration, so "registered" implies "passed `ImageValidationService`").
   For POD, the Printify product must also exist. Any failure: task is marked
   `BLOCKED_NO_PRODUCT` via `TaskService.record_pipeline_block()`, an alert
   fires, and `EtsyClient.create_draft_listing()` is **never called**.
5. `create_listing` — only reached if the gate passed
6. `attach_publish` — uploads images + the digital file. For digital
   products, if the digital file upload fails or never happens, this is now
   also treated as a gate failure *after the fact*: the draft listing is
   deleted via the new `EtsyClient.delete_listing()` and the task is blocked.
   (Etsy's digital-file endpoint requires a `listing_id` to exist first, so
   this precondition can't be checked strictly before creation — instead we
   roll back the listing if it fails.)
7. `printify_link` — links the pre-created Printify product to the real
   `listing_id` via new `PODFulfillmentService.set_etsy_listing_id()`
8. `pinterest` (independent of Etsy stages)

**New methods:**
- `EtsyClient.delete_listing(listing_id)` — `DELETE /v3/application/listings/{id}`.
- `PODFulfillmentService.set_etsy_listing_id(pod_product_id, etsy_listing_id)`.
- `TaskService.record_pipeline_block(task_id, reason)` — writes
  `output_data.pipeline_status = "BLOCKED_NO_PRODUCT"` and
  `output_data.pipeline_blocked_reason`. Does not touch `task.status` — the
  task's own QA/execution lifecycle already completed successfully; it's the
  downstream Etsy listing that was blocked. Surfaced via `output_data` so the
  dashboard can display it without changing the `TaskStatus` state machine.

**Related correctness fix:** `is_pod` was previously `task_type in {"pod",
"digital_download"}`, which meant pure digital-download tasks were listed as
physical (`shipping_profile_id`, quantity=1) and passed to
`PODFulfillmentService`/Printify. `is_pod` and `is_digital` are now distinct
checks, matching `ListingGeneratorAgent`'s actual "download" vs "physical"
semantics.

**Test:** `scripts/test_step90_product_gate.py` — proves, with test doubles
(no real Etsy/Printify calls):
- missing delivery asset → `create_draft_listing` never called, task blocked
- valid delivery asset → listing created normally
- failing Printify precheck (POD) → no listing created
- failed digital upload after listing creation → listing deleted, task blocked

---

### Fix 2 — Concept generation must produce ONE specific, concrete product

**`app/agents/trend_research_agent.py`** — `TrendResearchAgent` now extends
`BaseAgent` and makes a second LLM call after `IntelligenceAgent.synthesize()`:
given the broad market insight, it must return
`{product_name, product_type, description, target_audience, confidence}`.

Validation (`_validate_product`) rejects and retries (up to 3 attempts, same
retry-with-feedback pattern as `ImageValidationService.validate_with_retry`)
when:
- `product_name` is missing/empty or contains strategy-language markers
  ("niche market", "collection of", "focus on", "various products", etc.)
- `product_type` isn't `digital_download` or `pod`
- `description` is missing or doesn't reference the specific `product_name`

**`app/workers/autonomy_worker.py`** — now builds the task prompt from
`product_name` + `description` + `target_audience` (not the raw insight
sentence), and creates the task with `type=product_type` instead of the
previous hardcoded `type="general"` — so `PipelineOrchestrator`'s delivery-
asset gate actually engages for autonomy-created tasks.

**Tests updated/added:**
- `scripts/test_step88_autonomy.py` [8] — updated to the new agent output
  schema; asserts the task is created with the specific `product_name` in
  its prompt and `type` set to the concrete `product_type`.
- `scripts/test_step90_product_gate.py` [5]/[6] — `_validate_product` unit
  tests + a `run()` test proving a vague first attempt is rejected and
  retried until a specific, valid concept is produced.

---

### Cleanup

`EtsyClient` did not have a delete/deactivate method before this fix (now
added — see `delete_listing()` above). **The previously-created bad listing
("a collection of digital products and print-on-demand products") still
needs to be removed from the live Etsy account.** This was not done as part
of this change since it requires a real, authenticated call against Maj's
live Etsy shop — Maj should either delete that specific draft listing
manually from Etsy's Shop Manager UI, or explicitly ask for it to be deleted
programmatically (now possible via `EtsyClient.delete_listing()`).

---

### Verification

All 8 pre-existing test suites touched by this change still pass unchanged
(`test_step88_autonomy.py`, `test_step89_pipeline_orchestrator.py`,
`test_step81_pod_fulfillment.py`, `test_step83_stress.py`,
`test_step84_performance.py`, `test_step73_etsy_image_integration.py`).
`test_step90_product_gate.py` (6/6) is new, covering both fixes with test
doubles — zero real Etsy/Printify/OpenRouter API calls.

Live verification (one more real autonomy cycle or manually triggered task,
to confirm either a real specific product + real listing, or a clean
`BLOCKED_NO_PRODUCT` with no listing) was not run as part of this change —
recommended before flipping `AUTONOMY_ENABLED=true` again.
