# AI Factory — Automated Changelog

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
