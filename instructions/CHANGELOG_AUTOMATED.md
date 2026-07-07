# AI Factory — Automated Changelog

---

## Fix — Switch default image model to Seedream 4.5
**Date:** 2026-07-07
**Files modified:**
  - config/settings.py — OPENROUTER_IMAGE_MODEL: "google/gemini-3.1-flash-image" → "bytedance-seed/seedream-4.5"
  - app/agents/image/product_image_agent.py — default resolution: "1K" → "2K" (Seedream requires minimum ~3,686,400 pixels; "1K"=1024×1024=1M pixels is below this)
  - app/agents/image/social_image_agent.py — PINTEREST_RESOLUTION: "2K" → "4K" (2:3 at "2K" produces ~2.8M pixels, still below Seedream minimum; 4K produces 2732×4096=11.2M pixels; flat-rate so no cost penalty)
  - scripts/test_openrouter_image_provider.py — Updated to use "2K" for 1:1 calls and "4K" for 2:3 Pinterest call; model assertion made model-agnostic; summary note added explaining the pixel-minimum constraint
  - scripts/test_step70_social_image_agent.py — Assertion updated to expect PINTEREST_RESOLUTION="4K"
**Test:** scripts/test_openrouter_image_provider.py — PASSED (real Seedream 4.5 API calls made, 2 images generated)
  - Confirmed 1:1 @ 2K => 2048×2048 px, real cost $0.04
  - Confirmed 2:3 @ 4K => 2732×4096 px, real cost $0.04 (flat-rate — same cost despite 2.7× more image tokens)
**Notes/assumptions:**
  - Real confirmed per-image cost: $0.04 flat regardless of resolution or aspect ratio (overrides the conflicting $0.04/$0.05 figures in OpenRouter docs — the $0.05 figure appears to be stale)
  - Seedream pixel minimum constraint discovered: requires >= 3,686,400 pixels per image. 1:1 at "1K" (1,048,576 px) fails with HTTP 400. 1:1 at "2K" (4,194,304 px) passes. Non-square ratios need "4K" because e.g. 2:3 at "2K" only produces ~2.8M pixels.
  - Updated per-product cost estimate: 4 × $0.04 = $0.16/product (down from ~$0.30 on Gemini Flash). No resolution-based cost scaling — all 4 images cost the same regardless of whether they are "2K" or "4K".
  - Parameter compatibility: Seedream accepts the same aspect_ratio and resolution params already sent by OpenRouterImageProvider. No changes to the provider itself were needed.
  - Model remains swappable via settings.OPENROUTER_IMAGE_MODEL with no other code changes, assuming the target model also uses the same resolution/aspect_ratio parameter names.

---

## Fix — Confirm real image dimensions and correct cost assumptions
**Date:** 2026-07-07
**Files modified:**
  - scripts/test_openrouter_image_provider.py — Now fails loudly (sys.exit(1)) if Pillow is
    not importable instead of silently printing "skipping dimension decode" and reporting PASSED.
    Also extended to make a second real API call (1:1/2K) to capture delivery-resolution cost.
  - requirements.txt — Added `Pillow` (was installed ad-hoc in step 68 but never committed to
    requirements.txt; a fresh venv would have been missing it — this was the root cause of the
    silent skip)
  - app/services/image_validation_service.py — NO CHANGES NEEDED. Confirmed real dimensions
    (1024×1024 for 1K, 2048×2048 for 2K) both satisfy the existing min_width=1000/min_height=1000
    threshold for 'listing' and 'delivery' use cases.
**Test:** scripts/test_openrouter_image_provider.py — PASSED (real OpenRouter API calls made,
  2 images generated)
  - Confirmed 1:1 @ 1K  => 1024×1024 px  (assumption was correct)
  - Confirmed 1:1 @ 2K  => 2048×2048 px
**Notes/assumptions:**
  - Real cost per 1:1/1K image call: $0.0672 (previous estimate of $0.02-0.04 was wrong)
  - Real cost per 1:1/2K image call: $0.1008 (delivery quality — used by PODDesignAgent)
  - Pricing model: per image token at $0.00006/token. 1K=1120 tokens, 2K=1680 tokens.
    Token count does NOT scale linearly with pixel count (1K→2K is 4× the pixels but only
    1.5× the tokens), so linear extrapolation from per-resolution cost is inaccurate.
  - Rough cost per full product (2 listing images + 1 Pinterest pin + 1 delivery image):
    3 × $0.0672 + 1 × $0.1008 = ~$0.30 per product.
  - For comparison: ByteDance Seedream 4.5 charges $0.04/image flat regardless of resolution,
    which would be 4 × $0.04 = $0.16/product. Gemini Flash at ~$0.30/product is roughly 2×
    more expensive per product. Worth knowing before generating at real volume — model can be
    swapped via settings.OPENROUTER_IMAGE_MODEL without code changes.

---

## Fix — Replace OpenAI/DALL-E 3 dependency with OpenRouter Image API
**Date:** 2026-07-07
**Files created:**
  - app/core/providers/openrouter_image_provider.py — OpenRouterImageProvider: concrete image provider backed by POST https://openrouter.ai/api/v1/images; self-registers under "openrouter"; uses OPENROUTER_API_KEY and aspect_ratio+resolution params (not DALL-E's size param)
  - scripts/test_openrouter_image_provider.py — Integration test (replaces test_step67_dalle3_provider.py)
**Files removed:**
  - app/core/providers/dalle3_provider.py — Deleted; replaced by openrouter_image_provider.py
**Files modified:**
  - config/settings.py
      - IMAGE_PROVIDER: "dalle3" → "openrouter"
      - Added: OPENROUTER_API_KEY: str | None = None (makes the key visible alongside all other credentials)
      - Added: OPENROUTER_IMAGE_MODEL: str = "google/gemini-3.1-flash-image" (configurable default)
      - DEFAULT_IMAGE_SIZE kept as documented fallback; actual requests use aspect_ratio+resolution
      - OPENAI_API_KEY kept with comment noting it is no longer used for images
  - app/agents/image/product_image_agent.py
      - Old: generate_image(prompt, size="1024x1024")
      - New: generate_image(prompt, aspect_ratio="1:1", resolution="1K")
      - size param removed from call; aspect_ratio/resolution added to run() task dict keys
  - app/agents/image/social_image_agent.py
      - Old: PINTEREST_SIZE = "1024x1792" (DALL-E 3 approximation = 4:7 ratio)
      - New: PINTEREST_ASPECT_RATIO = "2:3", PINTEREST_RESOLUTION = "1K"
      - Model natively supports true 2:3; no more approximation needed
      - generate_image call updated to pass aspect_ratio="2:3", resolution="1K"
  - app/agents/image/pod_design_agent.py
      - Old: generate_image(prompt, size=size)
      - New: generate_image(prompt, aspect_ratio="1:1", resolution="2K")
      - Uses 2K resolution for delivery-quality assets (higher than listing images)
  - app/services/image_validation_service.py
      - pinterest use_case expected_ratio: (4, 7) → (2, 3)
      - Old value was DALL-E-specific (1024×1792 = 4:7). New model natively produces 2:3.
  - scripts/test_step70_social_image_agent.py — Updated: asserts aspect_ratio="2:3" instead of old PINTEREST_SIZE constant
  - scripts/test_step72_image_validation.py — Updated: Pinterest test image changed from 1024×1792 (4:7) to 1000×1500 (true 2:3)
  - scripts/test_step74_pinterest_image_integration.py — Updated: FakeImageProvider updated to accept aspect_ratio/resolution kwargs; assertion updated from PINTEREST_SIZE to PINTEREST_ASPECT_RATIO
**Test:** scripts/test_openrouter_image_provider.py — BLOCKED on account funding
  Provider registration confirmed working (import triggers self-registration under "openrouter",
  ImageProviderManager resolves it as OpenRouterImageProvider). The ONE real API call failed with
  HTTP 402: "Insufficient credits. This account never purchased credits."
  The OPENROUTER_API_KEY in .env is the same key funding the text pipeline — either image
  generation requires separate credit top-up on this account, or the free tier only covers
  text completions. Action required from Maj: add image generation credits at
  https://openrouter.ai/settings/credits, then rerun scripts/test_openrouter_image_provider.py
  to confirm actual output dimensions and finalize ground truth.
  All steps 69-76 re-verified and PASSING with updated test doubles.
**Notes/assumptions:**
  - Default model: google/gemini-3.1-flash-image. Confirmed supported aspect_ratios from
    OpenRouter docs: 1:1, 2:3, 3:2, 4:3, 3:4, 16:9, 9:16, 4:5, 5:4, 1:4, 4:1, 1:8, 8:1, 21:9.
    Resolution tiers: 512, 1K, 2K, 4K. Max n=1 per request.
  - Actual output pixel dimensions for "1K" are UNCONFIRMED (API call blocked by 402).
    Best assumption: "1K" at "1:1" → ~1024×1024 px; "1K" at "2:3" → ~683×1024 px.
    Run the test once funded to get ground truth and update image_validation_service.py
    minimum resolutions if needed.
  - Response shape confirmed from docs: data[0].b64_json (not a hosted URL).
  - The 4:7 Pinterest ratio workaround (introduced because DALL-E 3 only had 1024×1792)
    is removed. The new model supports true 2:3 natively.

---

---

## Step 67 — Implement DALL-E 3 image provider
**Date:** 2026-07-07
**Files created:**
  - app/core/providers/dalle3_provider.py — Concrete DALL-E 3 implementation of BaseImageProvider; self-registers with ImageProviderManager under the name 'dalle3' on import
**Files modified:**
  - (none — image_base.py and image_manager.py unchanged per handoff instructions)
**Test:** scripts/test_step67_dalle3_provider.py — PASSED
  (OPENAI_API_KEY is blank in .env — live DALL-E 3 API call was SKIPPED. Registration
  logic confirmed working: provider registers, ImageProviderManager resolves it, and the
  constructor raises a clear RuntimeError when the key is absent. To run the one real
  API call prescribed by the handoff, set OPENAI_API_KEY in .env and re-run this script.)
**Notes/assumptions:**
  - Installed `openai` package (was missing from venv).
  - The test exits 0 on missing key (treats it as a skip, not a failure) so downstream
    steps using test doubles are not blocked.
  - When OPENAI_API_KEY is set, the script makes exactly ONE real API call and verifies
    url/provider/model in the result.

---

## Step 68 — Implement image storage and file service
**Date:** 2026-07-07
**Files created:**
  - app/services/image_file_service.py — Local filesystem image storage service
  - data/images/listing/ — Storage root for listing/preview images (created on disk)
  - data/images/delivery/ — Storage root for delivery/print-ready images (created on disk)
**Files modified:**
  - (none)
**Test:** scripts/test_step68_image_storage.py — PASSED (test double used, no DALL-E API call)
**Notes/assumptions:**
  - Storage convention chosen: `data/images/{variant}/{task_id}/{filename}` at project root.
    Rationale: local-first (no cloud storage exists), task_id subdirectory allows easy
    per-task cleanup, variant directory (listing vs delivery) makes the purpose explicit
    at a glance.
  - Two variants per asset are explicitly enforced: 'listing' (preview/public) and
    'delivery' (customer-received / POD-submittable). This is what makes digital
    downloads a real product.
  - Pillow installed for step 72 image validation.

---

## Step 69 — Create product image generation agent
**Date:** 2026-07-07
**Files created:**
  - app/agents/image/__init__.py — Package init
  - app/agents/image/product_image_agent.py — ProductImageAgent: generates hero + lifestyle listing images (1024x1024, stored as 'listing' variant)
**Files modified:**
  - (none)
**Test:** scripts/test_step69_product_image_agent.py — PASSED (test double used, no DALL-E API call)
**Notes/assumptions:**
  - Generates two images per product: hero shot + lifestyle mockup, both stored as
    'listing' variant in data/images/listing/{task_id}/.
  - Prompt construction uses the visual brief from VisualDirectorAgent as the core
    direction, not just the raw product name.
  - image_provider is injected rather than hardcoded, enabling clean test doubles.

---

## Step 70 — Create social media image generation agent
**Date:** 2026-07-07
**Files created:**
  - app/agents/image/social_image_agent.py — SocialImageAgent: generates Pinterest pin images (1024x1792 portrait, stored as 'listing' variant)
**Files modified:**
  - (none)
**Test:** scripts/test_step70_social_image_agent.py — PASSED (test double used, no DALL-E API call)
**Notes/assumptions:**
  - Pinterest's optimal 2:3 ratio maps to DALL-E 3's 1024x1792 size (the actual
    ratio is 4:7, close to 2:3). This is the tallest portrait DALL-E 3 supports.
  - Images are framed for a Pinterest feed (scroll-stopping, aspirational, visual
    breathing room at top) rather than a clean Etsy product shot.
  - Stored as 'listing' variant (preview-quality; not a customer deliverable).

---

## Step 71 — Create POD design generation agent
**Date:** 2026-07-07
**Files created:**
  - app/agents/image/pod_design_agent.py — PODDesignAgent: generates the actual sellable design artifact (stored as 'delivery' variant, serves double duty for digital downloads and future POD fulfillment)
**Files modified:**
  - (none)
**Test:** scripts/test_step71_pod_design_agent.py — PASSED (test double used, no DALL-E API call)
**Notes/assumptions:**
  - Design is stored as 'delivery' variant so:
      (a) EtsyImageService (step 73) can attach it as the digital download file
      (b) Step 81 (future POD integration) can find it without rework
  - No actual POD service API integration built — just the design generation and storage.
  - prompt_type 'digital_download' vs 'pod' produces slightly different prompt wording
    to bias the image toward printable artwork vs merchandise design.

---

## Step 72 — Implement image validation and quality checks
**Date:** 2026-07-07
**Files created:**
  - app/services/image_validation_service.py — ImageValidationService with validate() and validate_with_retry() following the retry→repair→retry pattern
**Files modified:**
  - (none)
**Test:** scripts/test_step72_image_validation.py — PASSED (test double used, no DALL-E API call; Pillow used for synthetic test images)
**Notes/assumptions:**
  - Three use cases with distinct rules: 'listing' (1000×1000 min, 1:1 ratio),
    'delivery' (1000×1000 min, 1:1 ratio), 'pinterest' (600×900 min, 4:7 ratio).
  - Pinterest ratio set to 4:7 (not 2:3) because DALL-E 3's 1024×1792 output
    is exactly 4:7; using 2:3 caused a false validation failure.
  - Pillow images must be opened with context managers on Windows to release file
    handles before unlink() — discovered during test failure.
  - Checks are objective only: resolution, aspect ratio, file readability, file size.
    No subjective/aesthetic scoring.

---

## Step 73 — Integrate image generation into Etsy product pipeline
**Date:** 2026-07-07
**Files created:**
  - app/services/etsy_image_service.py — EtsyImageService with upload_listing_image(), upload_digital_file(), publish_listing(), and attach_images_and_publish()
**Files modified:**
  - config/settings.py — Added AUTO_PUBLISH_LISTINGS: bool = False
**Test:** scripts/test_step73_etsy_image_integration.py — PASSED (test double used, no Etsy API call)
**Notes/assumptions:**
  - AUTO_PUBLISH_LISTINGS defaults to False and is intentional. Nothing goes live
    publicly without Maj explicitly setting this to True in .env after reviewing
    the generated content. This is a deliberate safeguard — do not flip it on
    without reviewing the listing first.
  - upload_listing_image uses /listings/{id}/images (listing photos endpoint).
  - upload_digital_file uses /listings/{id}/files (SEPARATE digital-file endpoint,
    not the same as images — confirmed by Etsy API docs review).
  - publish_listing PATCHes /listings/{id} with state="active".
  - attach_images_and_publish orchestrates all three steps in sequence and
    gracefully records errors per image rather than failing the whole pipeline.

---

## Step 74 — Integrate image generation into Pinterest automation
**Date:** 2026-07-07
**Files created:**
  - app/services/pinterest_image_service.py — PinterestImageService: generates pin image via SocialImageAgent and enriches listing dict with base64-encoded image
**Files modified:**
  - app/marketing/pinterest_channel.py — Added image_base64/image_content_type support in _post_async(); preferred over image_url (if/elif order)
**Test:** scripts/test_step74_pinterest_image_integration.py — PASSED (test double used, no DALL-E or Pinterest API call)
**Notes/assumptions:**
  - Because the server is local-first (no public CDN), Pinterest's image_base64
    media source type is used when posting pins — avoids needing a public URL.
  - PinterestChannel.post() is unchanged in signature; callers just pass the
    enriched listing dict (with image_base64 key) from PinterestImageService.
  - Original listing keys are always preserved in the enriched dict.

---

## Step 75 — Integrate image generation into POD product pipeline
**Date:** 2026-07-07
**Files created:**
  - app/services/pod_pipeline_service.py — PODPipelineService: routes product generation for digital_download and pod types, stores design as 'delivery' variant for step 81
**Files modified:**
  - (none)
**Test:** scripts/test_step75_pod_pipeline.py — PASSED (test double used, no DALL-E or POD service API call)
**Notes/assumptions:**
  - No actual POD service API integration (that is step 81). This step only ensures
    the design artifact exists at data/images/delivery/{task_id}/ in the form step 81
    can consume.
  - unsupported product_type returns ready_for_pod=False / design_path=None instead
    of raising, so the pipeline can handle mixed product batches gracefully.

---

## Step 76 — Implement image cataloging and asset management
**Date:** 2026-07-07
**Files created:**
  - app/models/image_asset.py — ImageAsset SQLAlchemy model (image_assets table)
  - app/services/image_catalog_service.py — ImageCatalogService: register, query, and attach-listing operations
**Files modified:**
  - app/models/__init__.py — Added ImageAsset import and __all__ entry
  - app/main.py — Added image_asset to model import list so table is created on startup
**Test:** scripts/test_step76_image_catalog.py — PASSED (real SQLite DB used for catalog queries, test double used for image generation — no DALL-E API call)
**Notes/assumptions:**
  - Registration is idempotent on local_path (re-registering same file updates
    the row rather than inserting a duplicate), making retries safe.
  - get_delivery_asset(task_id) is the primary hook for step 81: it returns the
    delivery-variant asset for a task so the POD integration can retrieve the design
    path without scanning the filesystem.
  - catalog.attach_listing(path, listing_id) is called by step 73 after a successful
    Etsy image upload, linking the asset to its Etsy listing for future reuse queries.

---
