"""
Post-completion pipeline orchestrator.

Called by TaskProcessor immediately after a task transitions to DONE.
Chains image generation → hard product gate → Etsy listing creation → image
attachment / publish → Pinterest marketing → POD Printify product.

DEFAULT-DENY (step 91): task.type must be a recognized product_format
(see app/core/product_formats.py). Any other task.type — "seo_writing",
"general", or anything else — is NOT a product-listing task at all and the
entire pipeline is skipped; create_draft_listing() is never reachable for
it. (This closes a real bug: previously ANY task type that wasn't
"pod"/"digital_download" fell through the gate entirely and went straight
to listing creation with zero asset verification — exactly the original bug,
through a different door — e.g. the "/tasks/etsy/listing" endpoint hardcodes
type="seo_writing".)

For every recognized format, a real, validated, READBACK-VERIFIED product
artifact is a BLOCKING precondition — not a best-effort side step:
  - single-image formats: ImageCatalogService.get_delivery_asset() must
    return a real asset that already passed ImageValidationService (step 72).
  - pdf_planner_or_guide: PDFGenerationService must produce AND independently
    re-open (via pypdf) a PDF with exactly the expected page count. Any
    single page failing mid-sequence fails the whole PDF — never a partial
    deliverable.
  - pod_apparel_design: PODFulfillmentService.create_product_for_task() must
    succeed, which itself re-fetches the product from Printify to confirm
    the submitted image is really attached (not just that create returned
    200).
  - After a listing is created, the digital file upload (digital formats) and
    the listing-photo attachment (readback via Etsy's listing-images GET) are
    ALSO verified. Etsy's upload endpoints require a listing_id to exist
    first, so these can't be checked strictly before creation — instead, any
    failure here deletes the just-created listing and blocks the task, so
    nothing incomplete survives.

Stage order:
  1. listing_images    — ProductImageAgent hero + lifestyle (always, for
                          any recognized format)
  2. delivery_asset     — single image (PODPipelineService) or multi-page
                          PDF (PDFGenerationService), depending on format
  3. printify_precheck  — pod_apparel_design only; runs BEFORE listing
                          creation so a failure blocks it outright
  4. HARD GATE           — delivery asset must exist+verified; POD product
                          must exist. Any failure: task marked
                          BLOCKED_NO_PRODUCT, create_draft_listing() never
                          called.
  5. create_listing     — Etsy draft listing via EtsyClient
  6. attach_publish      — uploads images + digital file, then publish.
                          Digital-file failure or missing listing-image
                          readback both roll back (delete listing + block).
  7. printify_link       — link the precreated Printify product to the
                          real listing_id
  8. pinterest           — independent of Etsy stages
"""
import asyncio
import logging
import os  # 1-3: was missing; any bare os.* use previously raised NameError
from pathlib import Path
from typing import Optional

from app.services.task_service import TaskService
from app.services.log_service import LogService
from app.services.image_catalog_service import ImageCatalogService
from app.agents.image.product_image_agent import ProductImageAgent
from app.agents.image.social_image_agent import SocialImageAgent
from app.agents.etsy.listing_generator import ListingGeneratorAgent
from app.services.image_validation_service import ImageValidationService, ImageValidationError
from app.services.pod_pipeline_service import PODPipelineService
from app.services.pdf_generation_service import PDFGenerationService, PDFGenerationError
from app.services.etsy_client import EtsyClient
from app.services.etsy_image_service import EtsyImageService
from app.services.pod_fulfillment_service import PODFulfillmentService
from app.services.pinterest_image_service import PinterestImageService
from app.services.marketing_service import MarketingService
from app.marketing.pinterest_channel import PinterestChannel
from app.core.product_formats import PRODUCT_FORMATS

logger = logging.getLogger("ai-factory")


class PipelineOrchestrator:

    def __init__(self):
        self.task_service = TaskService()
        self.log_service = LogService()
        self.catalog = ImageCatalogService()

    # ── Public entry point ────────────────────────────────────────────────────

    def run_post_completion(self, task_id: str) -> dict:
        """
        Execute all downstream stages for a task that just reached DONE.
        Returns a per-stage report dict; never raises.
        """
        # #4: attribute every provider cost (image gen, vision-QA, text LLM) spent
        # while processing this task to task_id, so per-product cost/profit exists.
        from app.core.cost_context import cost_attribution
        with cost_attribution(task_id):
            report = self._run_post_completion(task_id)
        # #15: persist per-stage provenance (task_steps + agent_executions summary)
        # so which stage did what, at what cost, is auditable from the DB.
        try:
            from app.services.execution_log_service import ExecutionLogService
            if isinstance(report, dict) and report.get("stages"):
                ExecutionLogService().record_pipeline_run(task_id, report)
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: provenance logging failed for {task_id}: {e}")
        return report

    def _run_post_completion(self, task_id: str) -> dict:
        task = self.task_service.get_task(task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        task_type = task.type or "general"
        format_spec = PRODUCT_FORMATS.get(task_type)
        report: dict = {"task_id": task_id, "task_type": task_type, "stages": {}}

        if format_spec is None:
            report["skipped"] = f"task_type '{task_type}' is not a recognized product_format — no listing pipeline runs"
            return report

        output_data = task.output_data or {}
        product_name = (output_data.get("title") or task.prompt or "Product")[:140]
        visual_brief = output_data.get("description") or task.prompt or ""
        is_pod = format_spec["category"] == "pod"
        is_pdf = format_spec["delivery"] == "pdf"
        is_set = format_spec["delivery"] == "image_set"  # 7-1: wall_art_set_3
        digital_required = format_spec["category"] == "digital"

        # Fail FAST: a POD (physical) listing REQUIRES a shipping profile, or the
        # create call 400s ("shipping_profile_id is required") — but only AFTER
        # we've paid to generate the Printify product + images. Resolve (or
        # auto-create) the profile up front and block before any generation if it
        # can't be obtained, so a misconfiguration can't burn spend.
        if is_pod:
            from app.services.etsy_shipping_service import EtsyShippingService
            try:
                _ship_id = asyncio.run(EtsyShippingService().get_or_create())
            except Exception as _e:
                _ship_id = None
                logger.error(f"PipelineOrchestrator: shipping-profile resolve failed for {task_id}: {_e}")
            if not _ship_id:
                self._block_task(
                    task_id,
                    "POD listing needs a shipping profile but none could be resolved or "
                    "auto-created (check Etsy shipping settings / ETSY_SHOP_ORIGIN_* )",
                    report, pre_listing=True,
                )
                return report

        is_autonomy = bool((task.metadata_ or {}).get("source") == "autonomy_worker")

        # Real deliverable content used to GROUND format-aware marketing prompts
        # (e.g. pdf_planner_or_guide: the actual generated page topics, so hero/
        # lifestyle depict real interior pages instead of a generic book cover).
        content_context = self._marketing_content_context(is_pdf, output_data)

        # 1 — listing images.
        # No format generates independent text-to-image listing photos upfront:
        #   - DIGITAL (single-image + PDF): photos are DERIVED from the real
        #     delivery at step 2.6 (an independent hero/lifestyle depicts
        #     genuinely DIFFERENT content than the delivered file — the
        #     consistency gate rejects it and no remake fixes it; confirmed live
        #     on tasks e881c422 and the pdf blocks).
        #   - POD (P1-1): photos are the REAL Printify mockup renders of the
        #     uploaded design, fetched at step 4.5 after the Printify product
        #     exists — never a text-to-image guess of what the garment might look
        #     like. The only independent generation left is the POD *fallback*
        #     (Printify mockups unavailable), which IS consistency-gated at 4.5.
        derive_listing_from_delivery = digital_required
        image_paths = []

        # 2 — delivery asset (single image, multi-page PDF, or 3-piece set)
        set_result = None
        # 1-4: defined up front so the content-QA regen re-uses the SAME text-led
        # brief + overlay text (a text-led product regenerated with the plain
        # brief loses its words or bakes garbled model text in).
        display_text = None
        design_brief = visual_brief
        if is_pdf:
            design_path = self._stage_pdf_design(task, task_id, product_name, visual_brief, output_data, report)
        elif is_set:
            # 7-1: the set stage generates + content-QA's its 3 pieces internally,
            # and produces the gallery-wall delivery asset, mockups, and per-piece
            # digital files up front.
            set_result = self._stage_wall_art_set(task_id, product_name, visual_brief, report)
            if report.get("blocked"):
                return report
            design_path = set_result.get("triptych") if set_result else None
        else:
            # B-4: for text-led concepts, render the words deterministically —
            # generate a TEXT-FREE background, then overlay the exact text (Pillow).
            display_text = (task.metadata_ or {}).get("display_text") if (task.metadata_ or {}).get("text_led") else None
            if display_text:
                design_brief = (
                    f"{visual_brief}. IMPORTANT: create a purely DECORATIVE background with NO text, "
                    "no letters, no words, no captions — leave clear space in the center for text."
                )
            design_path = self._stage_pod_design(task_id, product_name, design_brief, task_type, report, display_text=display_text)

        # 2.5 — CONTENT-QUALITY GATE (step 96). The FIRST check that inspects
        # actual content, not structure — catches garbled/incoherent
        # image-model text (e.g. "2 þutter") that every structural gate missed.
        # Runs BEFORE anything is uploaded to Etsy/Printify: no point
        # taxonomy-checking or uploading content already known to be garbage.
        # PDFs are content-QA'd per-page inside PDFGenerationService during
        # generation, so only single-image formats are reviewed here.
        if design_path and not is_pdf and not is_set:
            design_path = self._stage_content_quality(
                task_id, design_path, product_name, visual_brief, task_type, report,
                design_brief=design_brief, display_text=display_text)
            if report.get("blocked"):
                return report

        # 2.6 — For DIGITAL single-image products, build the listing photos FROM
        # the now-content-verified delivery design as PERSPECTIVE-composited scene
        # previews (framed on a wall / on a desk). Each honestly depicts the exact
        # delivered design but is foreshortened so a screenshot isn't a clean flat
        # copy; formats where the preview IS the product (3-2 WATERMARK_FORMATS)
        # also get a tiled watermark baked in. CRUCIALLY the raw deliverable is
        # NOT added as a public listing photo — it is uploaded only as the buyer-
        # gated digital FILE in _stage_attach_publish (digital_file_path=
        # design_path). Uploading the clean file as a listing photo would let
        # anyone download the product for free from the preview.
        if design_path and derive_listing_from_delivery and not is_set:
            image_paths = self._build_listing_mockups(task_id, design_path, report, product_format=task_type)
        elif is_set and set_result:
            # 7-1: the set already built its gallery-wall mockups.
            image_paths = set_result.get("mockups") or []

        # 2.6b — A-5: build the multi-file delivery BUNDLE (extra print ratios /
        # device sizes / letter PDF) from the content-verified master, all pure
        # PIL (zero image-gen). PDFs (already the final file) and POD skip this.
        # The set already assembled its per-piece bundle in _stage_wall_art_set.
        digital_files = None
        bundle_note = ""
        if is_set and set_result:
            digital_files = set_result.get("digital_files") or []
            bundle_note = f"Set of 3 coordinated prints — {len(digital_files)} files across standard sizes."
            report["stages"]["delivery_bundle"] = {"ok": True, "files": len(digital_files)}
        elif design_path and digital_required and not is_pdf:
            from app.services.delivery_bundle_service import DeliveryBundleService
            digital_files = DeliveryBundleService().build(design_path, task_type)
            bundle_note = DeliveryBundleService.size_summary(task_type, len(digital_files or []))
            report["stages"]["delivery_bundle"] = {"ok": True, "files": len(digital_files or [])}

        # 2.7 — MARKETING/DELIVERABLE CONSISTENCY GATE (step 96): backstop that
        # every INDEPENDENTLY-GENERATED listing photo plausibly depicts the same
        # product as the delivery asset, blocking a buyer-misrepresentation
        # before listing. This only applies to POD/PDF, whose listing photos are
        # separate generations. For digital single-image formats the listing
        # photos are composited from the already content-verified delivery design
        # (step 2.6) — there is NO independent image that could misrepresent, so
        # the gate is a no-op. (Running it would false-positive: the vision model
        # reads the mockup's smaller-with-margins framing vs the full-bleed
        # delivery as "a different pose", then remakes into an independent
        # generation that genuinely differs — the exact loop that blocked real
        # tasks e881c422 / e725eb75.) The gate stays FULLY active for POD/PDF.
        if design_path and image_paths and not derive_listing_from_delivery:
            image_paths = self._stage_marketing_consistency(
                task_id, design_path, image_paths, product_name, visual_brief, is_autonomy, report,
                task_type=task_type, content_context=content_context,
            )
            if report.get("blocked"):
                return report
        elif derive_listing_from_delivery:
            report["stages"]["marketing_consistency"] = {
                "ok": True,
                "skipped": "listing photos composited from the content-verified delivery design — no independent marketing image to verify",
            }

        # 3 — POD physical: a real Printify product must exist BEFORE a listing
        # is created, so its failure can block listing creation outright.
        pod_product = None
        if is_pod:
            # P0-12: give the blueprint selector the REAL concept, not task_id=...
            pod_concept = product_name
            if visual_brief:
                pod_concept = f"{product_name}. {visual_brief}"
            pod_product = self._stage_printify_precheck(task_id, report, concept=pod_concept)

        # 4 — HARD GATE: no listing without a verified real product behind it.
        gate_error = self._delivery_gate_error(task_id, is_pdf, is_pod, pod_product)
        if gate_error:
            # DEEP AUDIT V2 #2: persist the SPECIFIC delivery failure (which page,
            # QA reason, page-count mismatch) into the block reason, not just the
            # generic "no verified PDF". The specifics used to go to stdout only and
            # were lost on recycle, making the 75% PDF block rate un-diagnosable.
            specific = (report.get("stages", {}).get("delivery_asset", {}) or {}).get("error")
            if specific:
                gate_error = f"{gate_error} [detail: {str(specific)[:180]}]"
            self._block_task(task_id, gate_error, report, pre_listing=True)
            return report

        # 4.5 — P1-1: POD listing photos are the REAL Printify mockup renders of
        # the uploaded design (guaranteed to depict the exact product — no
        # consistency gate needed, and 2 image generations saved). If none can be
        # fetched, fall back to independently-generated photos AND run the
        # marketing/deliverable consistency gate on that fallback (which may block).
        if is_pod and pod_product:
            image_paths = self._stage_pod_listing_images(task_id, pod_product, report)
            if image_paths:
                report["stages"].setdefault("marketing_consistency", {
                    "ok": True,
                    "skipped": "POD listing photos are real Printify mockups of the uploaded design — nothing independent to verify",
                })
            else:
                logger.warning(
                    f"PipelineOrchestrator: no Printify mockups for {task_id}; "
                    f"falling back to generated listing photos (consistency-gated)"
                )
                image_paths = self._stage_listing_images(
                    task_id, product_name, visual_brief, is_autonomy, report,
                    task_type=task_type, content_context=content_context,
                )
                if design_path and image_paths:
                    image_paths = self._stage_marketing_consistency(
                        task_id, design_path, image_paths, product_name, visual_brief, is_autonomy, report,
                        task_type=task_type, content_context=content_context,
                    )
                    if report.get("blocked"):
                        return report

        # 5 — create Etsy draft listing
        # P1-6: reconcile the listing's page-count claims with the REAL number of
        # PDF pages actually produced (sections can differ from the concept's
        # page_count), so the description never promises a different count.
        pdf_page_count = report["stages"].get("delivery_asset", {}).get("page_count") if is_pdf else None
        # A-2: ground the digital price in the real Etsy market median when available.
        _market = (task.metadata_ or {}).get("market") or {}
        market_price = _market.get("price_p50")
        market_titles = _market.get("top_titles")  # 2-4: proven-ranking title phrases
        # 5-1: if a previous (crashed) run already created a readback-verified
        # listing for this task, reuse it instead of creating a second one.
        existing_listing_id = (output_data or {}).get("listing_id")
        if existing_listing_id:
            listing_id = str(existing_listing_id)
            report["stages"]["create_listing"] = {"ok": True, "listing_id": listing_id,
                                                  "reused": "resumed — listing already existed"}
            logger.info(f"PipelineOrchestrator: resuming task {task_id} against existing listing {listing_id} (no re-create)")
        else:
            listing_id = self._stage_create_listing(task_id, product_name, output_data, task_type, is_pod, report, pod_product=pod_product, pdf_page_count=pdf_page_count, market_price=market_price, bundle_note=bundle_note, market_titles=market_titles)

        # 6 — attach images / digital file, then publish; readback-verify both
        if listing_id:
            self._stage_attach_publish(task_id, listing_id, image_paths, design_path, digital_required, report, digital_files=digital_files, alt_text_base=product_name)
        else:
            report["stages"]["attach_publish"] = {"skipped": "create_listing failed"}

        if report.get("blocked"):
            return report

        # 6b — 3-4: attach a short ken-burns video of the real design (free,
        # deterministic; boosts Etsy ranking + conversion). Best-effort.
        if listing_id and not report.get("blocked"):
            self._stage_listing_video(task_id, listing_id, report)

        # 7 — link the precreated Printify product to the now-real listing
        if is_pod and listing_id and pod_product:
            self._stage_link_printify_listing(pod_product, listing_id, report)
            # 7-2: push the size/color variations onto the Etsy listing so buyers
            # can pick — gated by POD_APPAREL_ENABLED (matches the multi-variant
            # Printify product created upstream).
            self._stage_pod_variations(pod_product, listing_id, report)

        # A failed create_listing leaves listing_id None — there is NO product a
        # buyer can open or purchase. Record the task as blocked (not silently
        # "done") so it is visible + retryable, and so marketing is suppressed
        # below. Skip if a stage already blocked it.
        if not listing_id and not report.get("blocked"):
            reason = (report.get("stages", {}).get("create_listing", {}) or {}).get("error") \
                or "Etsy listing creation failed — no live listing produced"
            self._block_task(task_id, f"listing not created: {reason}", report, pre_listing=False)

        # 8 — Social marketing (independent of Etsy stages): Pinterest + Tumblr.
        # ONLY when a real, live listing exists — never advertise a product that
        # buyers cannot actually open or buy (a pin/post to a nonexistent listing
        # is worse than none: it burns reach + looks broken).
        if listing_id and not report.get("blocked"):
            self._stage_pinterest(task_id, product_name, visual_brief, output_data, report, task_type=task_type, listing_id=listing_id)
            self._stage_tumblr(task_id, product_name, output_data, listing_id, report)

        # P0-9: stamp COMPLETED so a restart's resume scan won't re-run (and
        # re-spend on) this task. Only when a real listing was produced and the
        # task wasn't blocked — a create_listing failure stays unmarked so it can
        # be resumed/retried.
        if listing_id and not report.get("blocked"):
            try:
                self.task_service.mark_pipeline_completed(task_id, listing_id)
            except Exception as e:
                logger.warning(f"PipelineOrchestrator: failed to mark completed for {task_id}: {e}")

        self.log_service.info(
            source="PipelineOrchestrator",
            message=f"Post-completion pipeline finished for task {task_id}",
            payload={
                "task_id": task_id,
                "listing_id": listing_id,
                "stages_ok": [k for k, v in report["stages"].items() if v.get("ok")],
                "stages_failed": [k for k, v in report["stages"].items() if v.get("ok") is False],
            },
        )
        return report

    # ── Hard product gate ────────────────────────────────────────────────────

    def _delivery_gate_error(self, task_id: str, is_pdf: bool, is_pod: bool, pod_product) -> Optional[str]:
        """
        Returns a human-readable blocking reason if the required real product
        preconditions are not met, or None if the gate is satisfied.
        """
        asset = self.catalog.get_delivery_asset(task_id)
        if not asset or not Path(asset.local_path).exists():
            kind = "PDF" if is_pdf else "delivery asset"
            return f"no verified {kind} — generation, page-count readback, or validation failed"
        if is_pod and not pod_product:
            return "Printify product creation failed — no real POD product exists (or readback confirmation failed)"
        return None

    def _block_task(self, task_id: str, reason: str, report: dict, pre_listing: bool):
        logger.error(f"PipelineOrchestrator: task {task_id} BLOCKED_NO_PRODUCT — {reason}")
        try:
            self.task_service.record_pipeline_block(task_id, reason)
        except Exception as e:
            logger.error(f"PipelineOrchestrator: failed to record pipeline block for {task_id}: {e}")
        self._alert("Task blocked — no verified product behind listing", f"task_id={task_id}: {reason}")
        report["blocked"] = True
        report["blocked_reason"] = reason
        if pre_listing:
            report["stages"]["create_listing"] = {"skipped": f"blocked: {reason}"}

    def _cleanup_unbacked_listing(self, listing_id: str, report: dict):
        """
        Delete a draft listing that was created but turned out to have no
        real/complete product behind it. If deletion itself fails, alert so
        Maj can remove it manually from Etsy's Shop Manager UI.
        """
        try:
            asyncio.run(EtsyClient().delete_listing(listing_id))
            report["cleanup"] = {"listing_deleted": True, "listing_id": listing_id}
        except Exception as e:
            logger.error(f"PipelineOrchestrator: failed to delete unbacked listing {listing_id}: {e}")
            report["cleanup"] = {"listing_deleted": False, "listing_id": listing_id, "error": str(e)}
            self._alert(
                "Manual cleanup required — Etsy listing has no real product",
                f"listing_id={listing_id}: automatic delete failed ({e}). "
                "Please delete this draft listing manually in Etsy's Shop Manager.",
            )

    # ── Content-quality gate (step 96) ────────────────────────────────────────

    def _stage_content_quality(self, task_id, design_path, product_name, visual_brief, task_type, report,
                               design_brief=None, display_text=None):
        """
        Vision-model review of the delivered single-image asset for legible,
        coherent, correct, sellable content. On failure, REGENERATE the
        delivery asset and re-review up to CONTENT_QA_MAX_ATTEMPTS times, then
        block the task (same as every other gate failure). Returns the
        content-verified design path, or None if blocked.

        1-4: `design_brief` (the possibly text-free-background brief) and
        `display_text` are threaded so the regeneration re-applies B-4 exactly
        like the initial generation — a text-led product must keep its overlaid
        words on every retry, not lose them or bake in garbled model text.
        """
        from config import settings
        from app.services.content_quality_service import ContentQualityService

        svc = ContentQualityService()
        attempts = max(1, settings.CONTENT_QA_MAX_ATTEMPTS)
        current = design_path
        last_issues = ["content quality check did not pass"]

        max_color = float(getattr(settings, "COLORING_PAGE_MAX_COLOR_FRACTION", 0.03))

        for attempt in range(1, attempts + 1):
            # 1-5: deterministic pre-check for coloring pages — a pre-colored /
            # grey-shaded page is trivially detectable and must not rely on the
            # vision model noticing. If it's colored, fail this attempt (regen).
            if task_type == "coloring_page":
                try:
                    from app.core.coloring_page import color_fraction
                    frac = color_fraction(str(current))
                    if frac > max_color:
                        result = None
                        last_issues = [f"page is pre-colored — {frac*100:.1f}% of pixels are colored/shaded "
                                       f"(a coloring page must be clean black line art on white, <{max_color*100:.0f}%)"]
                        logger.warning(f"PipelineOrchestrator: coloring-page whiteness check failed for {task_id} "
                                       f"(attempt {attempt}): {frac*100:.1f}% colored")
                        report["stages"]["coloring_page_whiteness"] = {"colored_fraction": round(frac, 4), "attempt": attempt}
                        if attempt < attempts:
                            current = self._stage_pod_design(
                                task_id, product_name, design_brief or visual_brief, task_type, report,
                                display_text=display_text)
                            if not current:
                                break
                        continue
                except Exception as e:
                    logger.warning(f"PipelineOrchestrator: coloring-page whiteness check raised for {task_id}: {e}")

            # 2-1: a "seamless" pattern that doesn't tile IS a broken product
            # (refund/1-star generator). Enforce it deterministically like the
            # coloring-page check: edge mismatch above threshold fails the attempt.
            if task_type == "seamless_pattern":
                try:
                    from app.core.seamless import edge_mismatch
                    thresh = float(getattr(settings, "SEAMLESS_MAX_EDGE_MISMATCH", 22.0))
                    mism = edge_mismatch(str(current))
                    report["stages"]["seamless_check"] = {"edge_mismatch": round(mism, 1), "attempt": attempt}
                    if mism > thresh:
                        last_issues = [f"pattern does not tile — edge mismatch {mism:.1f} > {thresh:.0f}; "
                                       "a seamless pattern MUST continue smoothly across all four edges"]
                        logger.warning(f"PipelineOrchestrator: seamless_pattern {task_id} edge mismatch {mism:.1f} "
                                       f"(attempt {attempt}) — regenerating")
                        if attempt < attempts:
                            seam_brief = ((design_brief or visual_brief) +
                                          ". CRITICAL: the pattern MUST tile PERFECTLY across all four edges "
                                          "with no visible seam — edges must wrap continuously.")
                            current = self._stage_pod_design(task_id, product_name, seam_brief, task_type, report,
                                                             display_text=display_text)
                            if not current:
                                break
                        continue
                except Exception as e:
                    logger.warning(f"PipelineOrchestrator: seamless check raised for {task_id}: {e}")

            try:
                result = svc.review_asset_file(current, product_name, task_type, visual_brief)
            except Exception as e:
                logger.error(f"PipelineOrchestrator: content_quality raised for {task_id}: {e}")
                result = None
                last_issues = [f"content quality check raised: {e}"]

            if result and result.passed:
                report["stages"]["content_quality"] = {"ok": True, "attempt": attempt}
                return current

            if result:
                last_issues = result.specific_issues or ["content quality check did not pass"]
            logger.warning(
                f"PipelineOrchestrator: content quality failed for {task_id} "
                f"(attempt {attempt}/{attempts}): {last_issues}"
            )

            if attempt < attempts:
                # Regenerate the delivery asset (overwrites design.png) and re-review.
                # 1-4: reuse the SAME brief the initial call used (text-free
                # background for text-led products) and re-apply the text overlay.
                current = self._stage_pod_design(
                    task_id, product_name, design_brief or visual_brief, task_type, report,
                    display_text=display_text)
                if not current:
                    break

        reason = f"content quality gate failed: {'; '.join(last_issues)[:300]}"
        report["stages"]["content_quality"] = {"ok": False, "error": reason, "issues": last_issues}
        self._block_task(task_id, reason, report, pre_listing=True)
        return None

    def _stage_marketing_consistency(self, task_id, design_path, image_paths, product_name, visual_brief, is_autonomy, report, task_type=None, content_context=""):
        """
        Vision-model check that the listing/marketing photos plausibly depict
        the SAME product as the delivery asset.

        On a mismatch the delivery asset is NOT the problem (it already passed
        the content-quality gate) — only the independently-generated marketing
        photos are wrong. So instead of blocking the whole task, regenerate ONLY
        the specific mismatched image(s), feeding the vision model's own issue
        text back into the generation prompt as corrective guidance, then
        re-check — up to settings.MARKETING_CONSISTENCY_MAX_REMAKES total remake
        attempts PER TASK. If still failing after the cap, fall back to today's
        hard block. Returns the (possibly repaired) image path list, or None if
        blocked. The delivery asset is never regenerated and its position in the
        list is preserved.
        """
        from config import settings
        from app.services.content_quality_service import ContentQualityService

        svc = ContentQualityService()
        max_remakes = max(0, int(getattr(settings, "MARKETING_CONSISTENCY_MAX_REMAKES", 2)))
        current_paths = list(image_paths)
        # Ground truth for corrective feedback: the delivery asset was generated
        # from this same brief, so it describes the design the marketing images
        # must match.
        ground_truth = (visual_brief or product_name or "the delivered product design").strip()

        def _check():
            try:
                return svc.check_marketing_consistency(design_path, current_paths, product_name)
            except Exception as e:
                logger.error(f"PipelineOrchestrator: marketing_consistency raised for {task_id}: {e}")
                return None

        result = _check()
        last_issues = self._consistency_issues(result)
        remakes_used = 0
        anomalies_seen: list = []

        for remake in range(1, max_remakes + 1):
            if result and result.passed:
                break

            targets, anomalies = self._resolve_mismatch_targets(current_paths, design_path, result)

            # Loud, DISTINCT signal for genuinely unmappable indices (malformed /
            # out-of-range vision response) so this can never again look like
            # "the remake feature doesn't work" when it's actually "an edge case
            # wasn't handled". Different failure mode -> different, alerted log.
            if anomalies:
                anomalies_seen.extend(anomalies)
                self._alert_unmappable_mismatch(task_id, anomalies, len(current_paths))

            if not targets:
                # No marketing image we can safely regenerate (e.g. the only
                # flagged image IS the delivery asset, or no structured
                # breakdown and nothing but the delivery asset present).
                break

            logger.warning(
                f"PipelineOrchestrator: marketing/deliverable mismatch for {task_id} "
                f"(remake {remake}/{max_remakes}); regenerating marketing images "
                f"{[(i, r) for i, (r, _) in sorted(targets.items())]} with corrective feedback"
            )

            regenerated_any = False
            for idx, (role, issue) in targets.items():
                new_path = self._regenerate_marketing_image(
                    task_id, product_name, visual_brief, current_paths[idx],
                    role=role, corrective_issue=issue, ground_truth=ground_truth, report=report,
                    task_type=task_type, content_context=content_context,
                )
                if new_path:
                    current_paths[idx] = new_path
                    regenerated_any = True
            if not regenerated_any:
                break

            remakes_used = remake
            result = _check()
            last_issues = self._consistency_issues(result)

        if result and result.passed:
            stage = {"ok": True, "remakes": remakes_used}
            if anomalies_seen:
                stage["unmappable_indices"] = [a.get("image_index") for a in anomalies_seen]
            report["stages"]["marketing_consistency"] = stage
            return current_paths

        reason = f"marketing/deliverable mismatch: {'; '.join(last_issues)[:300]}"
        stage = {"ok": False, "error": reason, "issues": last_issues, "remakes": remakes_used}
        if anomalies_seen:
            stage["unmappable_indices"] = [a.get("image_index") for a in anomalies_seen]
        report["stages"]["marketing_consistency"] = stage
        self._block_task(task_id, reason, report, pre_listing=True)
        return None

    def _consistency_issues(self, result):
        """Human-readable issue list from a consistency result (or a vision error)."""
        if result is None:
            return ["marketing consistency check failed (vision call error)"]
        return result.specific_issues or ["marketing images do not match the delivered product"]

    def _marketing_role_for(self, path, design_str: str) -> str:
        """Best-effort role for a marketing image, so regeneration steers with the
        right prompt for ANY image in the set — not just hero/lifestyle. Roles are
        derived from the stable filenames ProductImageAgent writes (hero.png /
        lifestyle.png); any other listing image keeps a role from its filename
        stem so a future multi-image format is handled without code changes."""
        if str(path) == design_str:
            return "delivery"
        name = Path(path).name.lower()
        if "lifestyle" in name:
            return "lifestyle"
        if "hero" in name:
            return "hero"
        return Path(path).stem.lower() or "listing"

    def _resolve_mismatch_targets(self, current_paths, design_path, result):
        """
        Map the vision model's per-image mismatch reports onto the REAL, full
        marketing-image list — `current_paths` is exactly the set of images that
        were sent to and numbered by check_marketing_consistency, however many
        the product_format produced (2, 3, 4, …). This is format-agnostic: there
        is NO hardcoded assumption of "hero + lifestyle only".

        Returns (targets, anomalies):
          targets   : {index_in_current_paths: (role, issue_text)} for mappable,
                      non-delivery images to regenerate.
          anomalies : [{"image_index": j, "issue": str}] for indices that cannot
                      map to any real marketing image (non-int / out of range) —
                      a genuine internal inconsistency the caller alerts on.

        The delivery asset is never a target (confirmed correct); a mismatch
        reported against it is a benign skip, not an anomaly. With no structured
        per-image breakdown (older schema / unparseable), falls back to every
        non-delivery marketing image.
        """
        design_str = str(design_path)
        # marketing images are numbered 1..N over the images that actually exist,
        # in list order — matching how check_marketing_consistency sends them.
        existing = [(i, p) for i, p in enumerate(current_paths) if Path(p).exists()]
        targets: dict = {}
        anomalies: list = []

        mismatches = (result.mismatches if result else None) or []
        if mismatches:
            for m in mismatches:
                j = m.get("image_index")
                issue = m.get("issue") or "does not match the delivered design"
                if not isinstance(j, int) or not (1 <= j <= len(existing)):
                    anomalies.append({"image_index": j, "issue": issue})
                    continue
                orig_idx, p = existing[j - 1]
                if str(p) == design_str:
                    # Benign: the flagged image is the delivery asset itself
                    # (prepended as the primary listing photo) — never regenerated.
                    logger.debug(
                        f"PipelineOrchestrator: consistency flagged the delivery asset "
                        f"(marketing image {j}); skipping — it is confirmed correct"
                    )
                    continue
                targets[orig_idx] = (self._marketing_role_for(p, design_str), issue)
            return targets, anomalies

        generic = "; ".join(result.specific_issues) if (result and result.specific_issues) else "does not match the delivered design"
        for orig_idx, p in existing:
            if str(p) == design_str:
                continue
            targets[orig_idx] = (self._marketing_role_for(p, design_str), generic)
        return targets, anomalies

    def _alert_unmappable_mismatch(self, task_id: str, anomalies: list, n_images: int):
        """A mismatch was reported at an image_index that maps to no real
        regenerable asset. This is an internal inconsistency (malformed / out of
        range vision response), NOT the normal "images don't match" case — so it
        gets its own loud error log + alert with the exact index and task_id,
        keeping the two failure modes distinguishable."""
        idxs = [a.get("image_index") for a in anomalies]
        msg = (
            f"task_id={task_id}: consistency check reported mismatch at image_index "
            f"{idxs}, but only {n_images} marketing image(s) exist and none map to a "
            f"regenerable asset (malformed / out-of-range vision response). These "
            f"indices were skipped; the task will hard-block if the mappable images "
            f"don't resolve. This is an internal inconsistency, not a normal mismatch."
        )
        logger.error(f"PipelineOrchestrator: UNMAPPABLE consistency mismatch index — {msg}")
        self._alert("Consistency remake: unmappable mismatch index", msg)

    def _regenerate_marketing_image(self, task_id, product_name, visual_brief, target_path, role, corrective_issue, ground_truth, report, task_type=None, content_context=""):
        """
        Regenerate ONE mismatched marketing image in place (overwriting its
        file, so its path/catalog entry stay stable), steering it with the
        vision model's own issue text plus the delivery asset's ground-truth
        design description. Runs the SAME gates any freshly-generated image
        goes through (ImageValidationService + ContentQualityService) — a
        targeted retry does not get to skip them. Returns the new Path, or None
        if generation/validation/content-review failed (leaving the old image
        in place so the re-check still sees a mismatch and can eventually block).
        """
        from config import settings
        from app.services.content_quality_service import ContentQualityService

        target_path = Path(target_path)
        corrective_guidance = (
            "IMPORTANT: this marketing image MUST depict the SAME design as the "
            f"actual delivered product. The delivered product's real design is: "
            f"{ground_truth}. A previous version of THIS image was rejected because: "
            f"\"{corrective_issue}\". Do NOT show a different design, pattern, border, "
            "artwork, or text than the delivered product described above."
        )

        try:
            new_path = ProductImageAgent().regenerate_listing_image(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
                role=role,
                corrective_guidance=corrective_guidance,
                filename=target_path.name,
                product_format=task_type,
                content_context=content_context,
            )
        except Exception as e:
            logger.error(f"PipelineOrchestrator: targeted remake of {target_path.name} failed for {task_id}: {e}")
            return None

        new_path = Path(new_path)
        try:
            ImageValidationService().validate(new_path, use_case="listing")
        except ImageValidationError as ve:
            logger.warning(f"PipelineOrchestrator: regenerated {target_path.name} failed validation: {ve}")
            return None

        try:
            review = ContentQualityService().review_asset_file(
                new_path, product_name, "marketing_image", visual_brief
            )
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: content review of regenerated {target_path.name} raised: {e}")
            return None
        if not review.passed:
            logger.warning(
                f"PipelineOrchestrator: regenerated {target_path.name} failed content review: {review.specific_issues}"
            )
            return None

        try:
            self.catalog.register(
                task_id=task_id,
                local_path=str(new_path),
                variant="listing",
                use_case="listing",
                agent="ProductImageAgent",
                provider=settings.IMAGE_PROVIDER,
                model=settings.OPENROUTER_IMAGE_MODEL,
            )
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: failed to re-register regenerated {target_path.name}: {e}")

        return new_path

    # ── Stages ────────────────────────────────────────────────────────────────

    def _marketing_content_context(self, is_pdf: bool, output_data: dict) -> str:
        """Real deliverable content used to ground format-aware marketing prompts.

        For a PDF planner/guide this is the actual generated page topics
        (output_data['sections']) so the hero/lifestyle images depict real interior
        pages rather than an invented decorative cover. Empty for formats that
        don't need grounding (they ignore it).
        """
        if not is_pdf:
            return ""
        sections = (output_data or {}).get("sections") or []
        briefs = [str(s).strip() for s in sections if str(s).strip()]
        return "; ".join(briefs[:8])

    def _build_listing_mockups(self, task_id: str, delivery_path, report: dict, product_format: str = None) -> list:
        """Build attractive listing/ad PREVIEW photos from the real delivery design
        (digital single-image formats): the actual delivered design composited into
        realistic scenes (a framed print on a wall, a print in a desk flat-lay) via
        MockupService, at a PERSPECTIVE ANGLE. So each photo is a professional-
        looking ad that honestly depicts the delivered product but isn't a usable
        flat copy if screenshotted (the clean, straight file is delivered only after
        purchase, never as a public listing photo). These same assets are reused by
        the Tumblr/Pinterest marketing refresh. Returns validated + catalog-
        registered Paths (empty on failure).
        """
        from config import settings
        from app.services.image_file_service import ImageFileService
        from app.services.mockup_service import MockupService

        if not Path(delivery_path).exists():
            logger.warning(f"PipelineOrchestrator: delivery missing for mockups {task_id}: {delivery_path}")
            report["stages"]["listing_images"] = {"ok": False, "error": "delivery missing", "source": "delivery_mockup"}
            return []

        mockups = MockupService()
        fs = ImageFileService()
        validator = ImageValidationService()
        out = []
        page_pngs = []  # P2-8: track extracted temp pages so we can clean them up

        # (filename, builder) — for a PDF deliverable the mockups are built from
        # the REAL extracted pages (a page on a desk + a fan of several pages);
        # for a single-image deliverable, a framed print + a desk flat-lay.
        if Path(delivery_path).suffix.lower() == ".pdf":
            page_pngs = self._extract_pdf_pages(delivery_path, max_pages=4)
            if not page_pngs:
                logger.warning(f"PipelineOrchestrator: no extractable PDF pages for mockups {task_id}")
                report["stages"]["listing_images"] = {"ok": False, "error": "no extractable pdf pages", "source": "delivery_mockup"}
                return []
            # #8: hero is LANDSCAPE (>=2000px) to avoid Etsy grid cropping; the
            # rest are >=2000px square. Sizes come from settings (LISTING_HERO_*,
            # LISTING_IMAGE_SIZE) so they're tunable without a code change.
            _hw, _hh = int(settings.LISTING_HERO_W), int(settings.LISTING_HERO_H)
            _sq = int(settings.LISTING_IMAGE_SIZE)
            builders = [
                ("hero.png", "pdf_page", lambda: mockups.build_mockup_bytes(str(page_pngs[0]), role="flatlay", size=_hw, height=_hh, product_format=product_format)),
                ("lifestyle.png", "pdf_fan", lambda: mockups.build_flatlay_bytes([str(p) for p in page_pngs], size=_sq, product_format=product_format)),
            ]
        else:
            # A-8: more listing photos convert better; all are free PIL composites
            # of the already-verified design, and the P3-6 scene cache gives each
            # a different background for variety. 3-2: watermarked for formats
            # where the preview IS the product (see WATERMARK_FORMATS).
            _hw, _hh = int(settings.LISTING_HERO_W), int(settings.LISTING_HERO_H)
            _sq = int(settings.LISTING_IMAGE_SIZE)
            builders = [
                ("hero.png", "framed", lambda: mockups.build_mockup_bytes(str(delivery_path), role="framed", size=_hw, height=_hh, product_format=product_format)),
                ("lifestyle.png", "flatlay", lambda: mockups.build_mockup_bytes(str(delivery_path), role="flatlay", size=_sq, product_format=product_format)),
                ("styled.png", "framed", lambda: mockups.build_mockup_bytes(str(delivery_path), role="framed", size=_sq, product_format=product_format)),
                ("desk.png", "flatlay", lambda: mockups.build_mockup_bytes(str(delivery_path), role="flatlay", size=_sq, product_format=product_format)),
            ]

        for fname, role, build in builders:
            try:
                png = build()
                p = Path(fs.save_bytes(png, task_id, "listing", fname))
                # #8: the hero is intentionally landscape (not 1:1) — validate it
                # against its own ratio so the square-listing rule doesn't reject it.
                _ratio = (int(settings.LISTING_HERO_W), int(settings.LISTING_HERO_H)) if fname == "hero.png" else None
                validator.validate(p, use_case="listing", expected_ratio=_ratio)
                self.catalog.register(
                    task_id=task_id,
                    local_path=str(p),
                    variant="listing",
                    use_case="listing",
                    agent="DeliveryMockup",
                    provider="pil",
                    model=f"scene_composite:{role}",
                )
                out.append(p)
            except Exception as e:
                logger.warning(f"PipelineOrchestrator: listing mockup {fname} ({role}) failed for {task_id}: {e}")

        # P2-8: the extracted PDF page PNGs were written with delete=False; now
        # that mockups are built they're no longer needed — unlink them so they
        # don't accumulate on the container disk.
        for p in page_pngs:
            try:
                Path(p).unlink()
            except Exception:
                pass

        report["stages"]["listing_images"] = {"ok": bool(out), "count": len(out), "source": "delivery_mockup"}
        return out

    def _extract_pdf_pages(self, pdf_path, max_pages: int = 4) -> list:
        """Extract up to `max_pages` real page images from a Pillow-assembled PDF
        (one full-page image per page — same structure content_quality's
        _delivery_image_bytes relies on) to temporary PNGs, for building listing
        mockups from the ACTUAL delivered pages. Returns a list of Paths."""
        import tempfile
        from io import BytesIO  # noqa: F401 (kept for parity/readability)
        from pypdf import PdfReader

        pages = []
        try:
            reader = PdfReader(str(pdf_path))
            for page in reader.pages[:max_pages]:
                imgs = page.images
                if not imgs:
                    continue
                tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tf.close()
                imgs[0].image.convert("RGB").save(tf.name, format="PNG")
                pages.append(Path(tf.name))
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: failed to extract PDF pages from {pdf_path}: {e}")
        return pages

    def _stage_pod_listing_images(self, task_id: str, pod_product, report: dict) -> list:
        """P1-1: use the REAL Printify mockup renders of the uploaded design as
        the POD listing photos (front/lifestyle), instead of independent
        text-to-image guesses that often show a plausible-but-wrong garment.
        Downloads up to 3 mockups, validates them, registers and returns paths.
        Returns [] on any failure so the caller can fall back to generation."""
        import httpx
        from app.services.image_file_service import ImageFileService
        from app.services.printify_client import PrintifyClient
        from config import settings

        fs = ImageFileService()
        validator = ImageValidationService()
        saved = []
        try:
            printify_product_id = getattr(pod_product, "printify_product_id", None)
            if not printify_product_id:
                report["stages"]["listing_images"] = {"ok": False, "error": "pod product has no printify_product_id", "source": "printify_mockup"}
                return []
            product = PrintifyClient().get_product(printify_product_id)
            images = product.get("images", []) or []
            # Prefer the default / publishing-selected mockups first.
            images = sorted(images, key=lambda im: (not im.get("is_default"), not im.get("is_selected_for_publishing")))
            urls = [im.get("src") for im in images if im.get("src")][:6]  # A-8: up to 6 photos
            if not urls:
                report["stages"]["listing_images"] = {"ok": False, "error": "no Printify mockup images", "source": "printify_mockup"}
                return []
            for idx, url in enumerate(urls):
                try:
                    resp = httpx.get(url, timeout=60.0, follow_redirects=True)
                    resp.raise_for_status()
                    fname = "hero.png" if idx == 0 else f"mockup{idx}.png"
                    p = Path(fs.save_bytes(resp.content, task_id, "listing", fname))
                    validator.validate(p, use_case="listing")
                    self.catalog.register(
                        task_id=task_id, local_path=str(p), variant="listing",
                        use_case="listing", agent="PrintifyMockup",
                        provider="printify", model="product_mockup",
                    )
                    saved.append(p)
                except Exception as e:
                    logger.warning(f"PipelineOrchestrator: Printify mockup {idx} failed for {task_id}: {e}")
            report["stages"]["listing_images"] = {"ok": bool(saved), "count": len(saved), "source": "printify_mockup"}
            return saved
        except Exception as e:
            logger.error(f"PipelineOrchestrator: failed to fetch Printify mockups for {task_id}: {e}")
            report["stages"]["listing_images"] = {"ok": False, "error": str(e), "source": "printify_mockup"}
            return []

    def _stage_listing_images(self, task_id: str, product_name: str, visual_brief: str, is_autonomy: bool, report: dict, record_spend: bool = True, task_type: str = None, content_context: str = "") -> list:
        from config import settings

        saved_paths = []
        try:
            agent = ProductImageAgent()
            result = agent.generate_listing_images(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
                product_format=task_type,
                content_context=content_context,
            )

            validator = ImageValidationService()
            for label, path in [("hero", result.get("hero")), ("lifestyle", result.get("lifestyle"))]:
                if not path:
                    continue
                p = Path(path)
                try:
                    validator.validate(p, use_case="listing")
                    saved_paths.append(p)
                    self.catalog.register(
                        task_id=task_id,
                        local_path=str(p),
                        variant="listing",
                        use_case="listing",
                        agent="ProductImageAgent",
                        provider=settings.IMAGE_PROVIDER,
                        model=settings.OPENROUTER_IMAGE_MODEL,
                    )
                except ImageValidationError as ve:
                    logger.warning(f"PipelineOrchestrator: {label} image failed validation: {ve}")

            report["stages"]["listing_images"] = {"ok": True, "count": len(saved_paths)}

            # P0-13: image spend is now recorded per-call at the provider choke
            # point (OpenRouterImageProvider), so the old flat $0.20-per-task
            # record here (which under-counted PDF pages, mockups, remakes, pins)
            # is gone — recording it again would double-count.

        except Exception as e:
            logger.error(f"PipelineOrchestrator: listing_images failed for {task_id}: {e}")
            self._alert("Listing image generation failed", f"task_id={task_id}: {e}")
            report["stages"]["listing_images"] = {"ok": False, "error": str(e)}

        return saved_paths

    def _flatten_white_background(self, path) -> bool:
        """Whiten a line-art design's faint near-white/checkerboard background to
        pure white IN PLACE (the actual delivered file), preserving the black line
        art exactly. Only pixels that are near-white in ALL channels (min >= 234)
        are whitened, so real content (any pixel with a dark/coloured channel) is
        untouched. Best-effort — never fails the pipeline."""
        try:
            from PIL import Image, ImageChops
            im = Image.open(path).convert("RGB")
            r, g, b = im.split()
            mn = ImageChops.darker(ImageChops.darker(r, g), b)  # per-pixel min channel
            mask = mn.point(lambda v: 255 if v >= 234 else 0)   # near-white in every channel
            cleaned = Image.composite(Image.new("RGB", im.size, (255, 255, 255)), im, mask)
            cleaned.save(path)
            return True
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: white-background flatten failed for {path}: {e}")
            return False

    def _stage_pod_design(self, task_id: str, product_name: str, visual_brief: str, task_type: str, report: dict, display_text: str = None) -> Optional[Path]:
        from config import settings

        # PODPipelineService/PODDesignAgent's product_type label only affects
        # the generation prompt wording — map our format name to the
        # digital/pod label they already understand rather than touching them.
        mapped_type = "pod" if PRODUCT_FORMATS.get(task_type, {}).get("category") == "pod" else "digital_download"

        # P1-2: deliver the file in the shape the product actually needs (a phone
        # wallpaper is 9:16, not a 1:1 square). Non-square renders at 4K to clear
        # Seedream's ~3.69M-pixel floor.
        from app.core.product_formats import delivery_aspect_for, aspect_to_ratio
        delivery_aspect = delivery_aspect_for(task_type)
        delivery_resolution = "2K" if delivery_aspect == "1:1" else "4K"
        expected_ratio = aspect_to_ratio(delivery_aspect)

        # 1-8: a coloring page must be UNCOLORED — pure black line art on white.
        # There is no point selling a coloring page that's already half-colored.
        if task_type == "coloring_page":
            visual_brief = (
                f"{visual_brief}. STRICT COLORING-PAGE RULES: pure black OUTLINE line art on a "
                "COMPLETELY WHITE background. Absolutely NO colour, NO grey shading, NO gradients, "
                "NO filled/shaded areas, and NO pre-colored sections anywhere — only clean black "
                "outlines that a person colors in themselves. The whole page is white except the "
                "black line work."
            )

        # B-3: steer seamless_pattern generation toward a tileable result.
        if task_type == "seamless_pattern":
            visual_brief = (
                f"{visual_brief}. IMPORTANT: a SEAMLESS repeating tileable pattern — the "
                "design must continue smoothly across all four edges so it tiles with no "
                "visible seam. Even, all-over motif; no borders, no framing, no single "
                "centered subject."
            )

        try:
            result = PODPipelineService().build_product_record(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
                product_type=mapped_type,
                aspect_ratio=delivery_aspect,
                resolution=delivery_resolution,
            )
            design_str = result.get("design_path")
            if not design_str:
                report["stages"]["delivery_asset"] = {"ok": False, "error": "no design artifact was generated"}
                return None

            design_path = Path(design_str)

            # Coloring pages are black line art meant to print on WHITE. Seedream
            # bakes a faint grey "transparency" checkerboard into the background;
            # flatten it to pure white in the ACTUAL delivered file (the buyer's
            # download) — preserving the line art exactly — before it's validated,
            # registered, or used to build the listing mockups.
            if task_type == "coloring_page":
                self._flatten_white_background(design_path)

            # B-4: render the exact words onto the text-free background BEFORE QA,
            # so content-quality sees pixel-crisp, correctly-spelled typography.
            if display_text:
                from app.services.text_overlay_service import TextOverlayService
                if TextOverlayService().overlay(str(design_path), display_text):
                    report["stages"]["text_overlay"] = {"ok": True, "text": display_text[:60]}

            # B-3: report the seamless-tiling quality of a seamless_pattern (soft
            # signal — logged, not blocking, since image models rarely tile perfectly).
            if task_type == "seamless_pattern":
                try:
                    from app.core.seamless import edge_mismatch, is_seamless
                    mismatch = edge_mismatch(str(design_path))
                    report["stages"]["seamless_check"] = {"edge_mismatch": round(mismatch, 1), "seamless": is_seamless(str(design_path))}
                    if mismatch > 22.0:
                        logger.warning(f"PipelineOrchestrator: seamless_pattern {task_id} edge mismatch {mismatch:.1f} (not perfectly tileable)")
                except Exception:
                    pass

            try:
                ImageValidationService().validate(design_path, use_case="delivery", expected_ratio=expected_ratio)
                self.catalog.register(
                    task_id=task_id,
                    local_path=str(design_path),
                    variant="delivery",
                    use_case="delivery",
                    agent="PODDesignAgent",
                    provider=settings.IMAGE_PROVIDER,
                    model=settings.OPENROUTER_IMAGE_MODEL,
                )
            except ImageValidationError as ve:
                logger.warning(f"PipelineOrchestrator: delivery image failed validation: {ve}")
                report["stages"]["delivery_asset"] = {"ok": False, "error": f"validation failed: {ve}"}
                return None

            report["stages"]["delivery_asset"] = {"ok": True, "design_path": design_str}
            return design_path
        except Exception as e:
            logger.error(f"PipelineOrchestrator: delivery_asset (single image) failed for {task_id}: {e}")
            self._alert("Delivery asset generation failed", f"task_id={task_id}: {e}")
            report["stages"]["delivery_asset"] = {"ok": False, "error": str(e)}
            return None

    def _stage_wall_art_set(self, task_id: str, product_name: str, visual_brief: str, report: dict) -> Optional[dict]:
        """7-1: generate a coordinated SET of 3 wall-art prints (shared palette/
        theme), content-QA each, verify they actually match, then assemble the
        gallery-wall listing photo + the per-piece print-ratio delivery bundle.
        Returns {triptych, pieces, digital_files, mockups} or None (blocked)."""
        from config import settings
        from app.services.wall_art_set_service import WallArtSetService, SET_SIZE
        from app.services.content_quality_service import ContentQualityService
        from app.services.delivery_bundle_service import DeliveryBundleService
        from app.services.image_file_service import ImageFileService
        from app.services.image_validation_service import ImageValidationService

        briefs = WallArtSetService.piece_briefs(product_name, visual_brief)
        cq = ContentQualityService()
        pieces = []
        try:
            for i, brief in enumerate(briefs, start=1):
                # 1-3: generate each piece straight to a DISTINCT filename so the
                # three pieces never overwrite each other (they share one task_id
                # and would all land on design.png otherwise). No rename needed.
                res = PODPipelineService().build_product_record(
                    task_id=task_id,
                    product_name=f"{product_name} (piece {i} of {SET_SIZE})",
                    visual_brief=brief,
                    product_type="digital_download",
                    aspect_ratio="1:1",
                    resolution="2K",
                    filename=f"set_piece_{i}.png",
                )
                piece_str = res.get("design_path")
                if not piece_str:
                    report["stages"]["wall_art_set"] = {"ok": False, "error": f"piece {i} did not generate"}
                    return None
                piece_path = Path(piece_str)
                # sanity: each piece must be its own file (guards against any
                # future regression that silently reuses one path for all three).
                if piece_path in pieces:
                    self._block_task(task_id, f"wall-art set piece {i} reused an earlier piece's path {piece_path} — refusing to ship a duplicate set", report, pre_listing=True)
                    report["stages"]["wall_art_set"] = {"ok": False, "error": f"piece {i} path collision"}
                    return None
                # content-QA each piece (block the whole set if any is garbage)
                try:
                    result = cq.review_asset_file(str(piece_path), product_name, "single_print", brief)
                    if not result.passed:
                        self._block_task(task_id, f"wall-art set piece {i} failed content QA: {result.specific_issues}", report, pre_listing=True)
                        report["stages"]["wall_art_set"] = {"ok": False, "error": f"piece {i} content QA failed"}
                        return None
                except Exception as e:
                    logger.warning(f"PipelineOrchestrator: set piece {i} QA raised for {task_id}: {e}")
                pieces.append(piece_path)

            # 2-2: do the 3 pieces actually share a palette? A clashing "coordinated
            # set" is a bad product — no longer just a log line. Regenerate the
            # single OUTLIER piece once, matched to the others' palette; if it still
            # clashes, block the task.
            tol = float(getattr(settings, "WALL_ART_SET_PALETTE_TOL", 0.42))
            consistency = WallArtSetService.palette_consistent([str(p) for p in pieces], tol=tol)
            if not consistency["consistent"]:
                logger.warning(f"PipelineOrchestrator: wall-art set {task_id} palette mismatch "
                               f"(max_distance={consistency['max_distance']} > {tol}) — regenerating outlier")
                if len(pieces) == 3:
                    oi = self._palette_outlier(consistency.get("pairs") or [], len(pieces))
                    good = [p for i, p in enumerate(pieces) if i != oi]
                    try:
                        palette = WallArtSetService.dominant_palette(str(good[0]))
                        pal_str = ", ".join(f"rgb{tuple(c)}" for c in (palette or [])[:4])
                        regen_brief = (f"{visual_brief}. CRITICAL: MATCH the exact color palette of the other "
                                       f"prints in this set ({pal_str}) — same colors, style, and mood so all "
                                       "three hang together as one gallery-wall set.")
                        res = PODPipelineService().build_product_record(
                            task_id=task_id, product_name=f"{product_name} (piece {oi+1} recolor)",
                            visual_brief=regen_brief, product_type="digital_download",
                            aspect_ratio="1:1", resolution="2K", filename=f"set_piece_{oi+1}.png")
                        newp = res.get("design_path")
                        if newp:
                            pieces[oi] = Path(newp)
                            consistency = WallArtSetService.palette_consistent([str(p) for p in pieces], tol=tol)
                    except Exception as e:
                        logger.warning(f"PipelineOrchestrator: outlier recolor failed for {task_id}: {e}")
                if not consistency["consistent"]:
                    self._block_task(task_id, f"wall-art set pieces clash (palette max_distance "
                                     f"{consistency['max_distance']} > {tol}) after outlier regen", report, pre_listing=True)
                    report["stages"]["wall_art_set"] = {"ok": False, "error": "palette mismatch after regen"}
                    return None

            # gallery-wall listing photo, registered as the delivery asset so the
            # hard delivery gate is satisfied by a single representative artifact.
            ifs = ImageFileService()
            ifs.delivery_dir(task_id).mkdir(parents=True, exist_ok=True)
            triptych = str(ifs.delivery_dir(task_id) / "set_triptych.png")
            WallArtSetService.compose_triptych([str(p) for p in pieces], triptych)
            try:
                ImageValidationService().validate(Path(triptych), use_case="delivery")
            except Exception as ve:
                logger.warning(f"PipelineOrchestrator: triptych validation soft-failed: {ve}")
            self.catalog.register(
                task_id=task_id, local_path=triptych, variant="delivery",
                use_case="delivery", agent="WallArtSetService",
                provider=settings.IMAGE_PROVIDER, model=settings.OPENROUTER_IMAGE_MODEL,
            )

            # buyer downloads: each piece + its standard print-ratio variants.
            digital_files = []
            for p in pieces:
                digital_files.append(str(p))
                try:
                    digital_files.extend(DeliveryBundleService().build(str(p), "single_print") or [])
                except Exception as e:
                    logger.warning(f"PipelineOrchestrator: set bundle for {p} failed: {e}")
            # de-dup while preserving order
            seen, files = set(), []
            for f in digital_files:
                if f and f not in seen:
                    seen.add(f); files.append(f)

            # listing photos: the gallery-wall scene view (perspective previews) + each
            # individual piece as its own mockup.
            mockups = self._build_listing_mockups(task_id, Path(triptych), report) or []

            report["stages"]["wall_art_set"] = {
                "ok": True, "pieces": len(pieces),
                "palette_consistent": consistency["consistent"],
                "palette_max_distance": consistency["max_distance"],
                "delivery_files": len(files),
            }
            return {"triptych": Path(triptych), "pieces": pieces, "digital_files": files, "mockups": mockups}
        except Exception as e:
            logger.error(f"PipelineOrchestrator: wall-art set failed for {task_id}: {e}")
            self._alert("Wall-art set generation failed", f"task_id={task_id}: {e}")
            report["stages"]["wall_art_set"] = {"ok": False, "error": str(e)}
            return None

    @staticmethod
    def _palette_outlier(pairs: list, n: int) -> int:
        """2-2: the piece index with the greatest total palette distance to the
        others (the one to regenerate)."""
        totals = [0.0] * n
        for pr in pairs or []:
            a, b, d = pr.get("a"), pr.get("b"), pr.get("distance", 0.0)
            if a is not None and b is not None:
                totals[a] += d
                totals[b] += d
        return max(range(n), key=lambda i: totals[i]) if n else 0

    def _stage_pdf_design(self, task, task_id: str, product_name: str, visual_brief: str, output_data: dict, report: dict) -> Optional[Path]:
        from config import settings

        page_briefs = self._resolve_pdf_page_briefs(task, output_data)

        try:
            pdf_path = PDFGenerationService().generate_pdf(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
                page_briefs=page_briefs,
                render_interior=getattr(settings, "PLANNER_RENDER_INTERIOR", True),
            )
            # PDFs aren't images — ImageValidationService's pixel/ratio checks
            # don't apply. PDFGenerationService already performed its own
            # stronger check (real per-page generation + independent pypdf
            # readback of the actual page count), so register directly.
            self.catalog.register(
                task_id=task_id,
                local_path=str(pdf_path),
                variant="delivery",
                use_case="delivery",
                agent="PDFGenerationService",
                provider=settings.IMAGE_PROVIDER,
                model=settings.OPENROUTER_IMAGE_MODEL,
            )
            report["stages"]["delivery_asset"] = {"ok": True, "design_path": str(pdf_path), "page_count": len(page_briefs)}
            return pdf_path
        except PDFGenerationError as e:
            logger.warning(f"PipelineOrchestrator: PDF delivery asset failed for {task_id}: {e}")
            report["stages"]["delivery_asset"] = {"ok": False, "error": str(e)}
            return None
        except Exception as e:
            logger.error(f"PipelineOrchestrator: PDF delivery_asset failed unexpectedly for {task_id}: {e}")
            self._alert("PDF delivery asset generation failed", f"task_id={task_id}: {e}")
            report["stages"]["delivery_asset"] = {"ok": False, "error": str(e)}
            return None

    def _resolve_pdf_page_briefs(self, task, output_data: dict) -> list:
        """
        Derive one content brief per PDF page. Prefers output_data['sections']
        (already generated by the SEO/QA stage — genuinely differentiated
        per-page topics, e.g. "Coffee Purchase Tracker", "Favorite Coffee
        Brews") over a generic fallback. Always truncated to
        settings.MAX_PDF_PAGES — the cap must hold even if an upstream stage
        (unaware of the PDF page cap) generated more sections than that.
        """
        from config import settings

        sections = output_data.get("sections") or []
        if sections:
            return [str(s) for s in sections[: settings.MAX_PDF_PAGES]]

        page_count = (task.metadata_ or {}).get("page_count")
        try:
            page_count = int(page_count)
        except (TypeError, ValueError):
            page_count = 1
        page_count = max(1, min(page_count, settings.MAX_PDF_PAGES))
        return [f"Page {i}" for i in range(1, page_count + 1)]

    def _stage_printify_precheck(self, task_id: str, report: dict, concept: Optional[str] = None):
        """
        Create the real Printify product BEFORE any Etsy listing exists, so a
        failure here can block listing creation outright (per the hard gate).
        PODFulfillmentService.create_product_for_task() already re-fetches
        the product from Printify to confirm the image is really attached.
        Does not pass etsy_listing_id yet — _stage_link_printify_listing
        wires it up once a real listing_id exists.
        """
        try:
            pod = PODFulfillmentService().create_product_for_task(task_id, concept=concept)
            report["stages"]["printify_product"] = {
                "ok": True, "pod_product_id": pod.id,
                "price_cents": getattr(pod, "price_cents", None),
                "cost_cents": getattr(pod, "cost_cents", None),
            }
            return pod
        except Exception as e:
            logger.error(f"PipelineOrchestrator: printify_product precheck failed for {task_id}: {e}")
            self._alert("Printify product creation failed", f"task_id={task_id}: {e}")
            report["stages"]["printify_product"] = {"ok": False, "error": str(e)}
            return None

    def _stage_link_printify_listing(self, pod_product, listing_id: str, report: dict):
        try:
            PODFulfillmentService().set_etsy_listing_id(pod_product.id, listing_id)
            report["stages"]["printify_product"]["etsy_listing_id"] = listing_id
        except Exception as e:
            logger.error(
                f"PipelineOrchestrator: failed to link Printify product {pod_product.id} "
                f"to listing {listing_id}: {e}"
            )

    def _stage_pod_variations(self, pod_product, listing_id: str, report: dict):
        """7-2: build the Etsy size/color inventory from the Printify product's
        variants (priced per-variant from real Printify cost) and PUT it onto the
        listing. Best-effort and gated — never fails a publish."""
        # Push Etsy size/color variations whenever the POD product has MULTIPLE
        # variants (apparel size x color, or mug/poster sizes). Single-variant POD
        # products need no variations. Format-agnostic — works for any POD type.
        if len(getattr(pod_product, "variant_ids", None) or []) <= 1:
            report["stages"]["pod_variations"] = {"skipped": "single variant"}
            return
        try:
            from app.services.pod_variant_mapper import PodVariantMapper
            from app.services.printify_client import PrintifyClient
            from app.services.pod_fulfillment_service import PODFulfillmentService

            product = PrintifyClient().get_product(str(pod_product.printify_product_id))
            enabled_ids = set(pod_product.variant_ids or [])
            pv = [v for v in (product.get("variants") or []) if v.get("id") in enabled_ids] or (product.get("variants") or [])

            def price_cents_fn(variant):
                cost = variant.get("cost")
                if cost:
                    return PODFulfillmentService._pod_price_cents_from_cost(int(cost))
                return int(pod_product.price_cents or 0)

            inventory = PodVariantMapper.build_etsy_inventory(pv, price_cents_fn)
            if not inventory.get("products"):
                report["stages"]["pod_variations"] = {"skipped": "no mappable variants"}
                return
            import asyncio
            asyncio.run(EtsyClient().update_listing_inventory(listing_id, inventory))
            report["stages"]["pod_variations"] = {"ok": True, "variations": len(inventory["products"])}
            logger.info(f"PipelineOrchestrator: pushed {len(inventory['products'])} Etsy variations to {listing_id}")
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: pod_variations failed for {listing_id}: {e}")
            report["stages"]["pod_variations"] = {"error": str(e)[:200]}

    def _stage_create_listing(self, task_id: str, product_name: str, output_data: dict, task_type: str, is_pod: bool, report: dict, pod_product=None, pdf_page_count: Optional[int] = None, market_price: Optional[float] = None, bundle_note: str = "", market_titles: Optional[list] = None) -> Optional[str]:
        from app.services.etsy_shipping_service import EtsyShippingService
        from app.services.etsy_client import DIGITAL_WHEN_MADE, POD_WHEN_MADE

        intended_taxonomy_id = PRODUCT_FORMATS.get(task_type, {}).get("taxonomy_id", 1)
        # Digital downloads must NOT be made_to_order or Etsy hides the
        # instant-download file slot in its editor (confirmed live on
        # 4534427807). made_to_order is correct for POD physical goods.
        intended_when_made = POD_WHEN_MADE if is_pod else DIGITAL_WHEN_MADE

        from app.core.product_formats import price_band_for, clamp_price
        band_lo, band_hi = price_band_for(task_type)

        try:
            # D-2b: build the listing directly from the executor's output_data +
            # deterministic tag derivation — NO ListingGeneratorAgent LLM call.
            # That call produced price/category/quantity/shipping which are ALL
            # overridden below for product formats, so it was pure wasted spend
            # and an extra JSON-parse failure mode per task.
            gen = ListingGeneratorAgent()
            # 2-4: pad tags with proven-ranking n-grams mined from the real winning
            # Etsy titles for this niche (trademark-filtered), not just name fragments.
            extra_terms = gen.title_ngrams(market_titles) if market_titles else None
            listing = {
                "product_name": product_name,
                "title": output_data.get("title", ""),
                "description": output_data.get("description", ""),
                "tags": gen._derive_tags(output_data.get("keywords", []), product_name=product_name, extra_terms=extra_terms),
                "sections": output_data.get("sections", []),
                "materials": [],
                "currency": "USD",
                "price": None,  # clamped/grounded below
            }
            listing["taxonomy_id"] = intended_taxonomy_id
            listing["when_made"] = intended_when_made
            # B-7: route the listing into its shop section when mapped.
            from config import settings as _sec_settings
            section_id = (getattr(_sec_settings, "SHOP_SECTION_MAP", None) or {}).get(task_type)
            if section_id:
                listing["shop_section_id"] = section_id

            # C-1: final trademark screen on the tags (rights-holders' bots scan
            # tags) — drop any that slipped through, and block outright if the
            # title itself carries a brand term (should never happen — the
            # concept was screened — but fail closed if it does).
            from app.core.trademark_screen import filter_tags, find_trademark
            clean_tags, dropped = filter_tags(listing.get("tags", []))
            if dropped:
                logger.warning(f"PipelineOrchestrator: dropped trademarked tags for {task_id}: {dropped}")
            listing["tags"] = clean_tags
            title_hit = find_trademark(listing.get("title", "")) or find_trademark(product_name)
            if title_hit:
                raise RuntimeError(f"listing title/name contains a trademarked term '{title_hit}' — refusing to publish")

            # A-4: real per-format materials (was always empty) + deterministic
            # buyer-question-answering description blocks (WHAT YOU GET / HOW IT
            # WORKS / TERMS) appended to the LLM's creative hook.
            from app.core.product_formats import materials_for, description_blocks
            listing["materials"] = materials_for(task_type)
            blocks = description_blocks(task_type, pdf_page_count)
            if bundle_note:
                blocks = blocks + "\n" + bundle_note  # A-5: "Includes N sizes: ..."
            if "WHAT YOU GET" not in (listing.get("description") or ""):
                listing["description"] = (listing.get("description") or "").rstrip() + "\n\n" + blocks

            # C-2: append the honest AI-assisted-design disclosure (Etsy requires
            # accurate "how it's made" info).
            from config import settings as _settings
            disclosure = getattr(_settings, "SHOP_AI_DISCLOSURE", "")
            if disclosure and disclosure.lower() not in (listing.get("description") or "").lower():
                listing["description"] = (listing.get("description") or "").rstrip() + f"\n\n{disclosure}"

            # P1-6: make the description's page-count truthful. Rewrite any
            # "N-page"/"N pages" claim to the REAL count and append an explicit
            # line, so a buyer never receives a different number of pages than
            # the listing promised.
            if pdf_page_count:
                import re
                desc = listing.get("description") or ""
                desc = re.sub(r"\b\d+(?=[\s-]?pages?\b)", str(pdf_page_count), desc, flags=re.I)
                if "printable page" not in desc.lower():
                    desc = desc.rstrip() + f"\n\nIncludes {pdf_page_count} printable pages."
                listing["description"] = desc

            # P0-11: never let a None/0/out-of-band LLM price reach Etsy — clamp
            # into the format's band (midpoint if invalid). Etsy rejects <$0.20.
            listing["price"] = clamp_price(listing.get("price"), task_type)

            # A-2: prefer the real Etsy market median when it falls inside our
            # sane band — grounds price in what buyers actually pay for this niche
            # rather than a band midpoint guess. (POD's cost-based price, set
            # below, still overrides this to protect margin.)
            if market_price and band_lo <= float(market_price) <= band_hi:
                listing["price"] = round(float(market_price), 2)
            elif market_price:
                # 3-3: the real market median exists but is OUTSIDE the band, so
                # it's silently discarded (likely underpricing). Record a
                # price_band_clamp event — after a few weeks these say exactly
                # which bands need recalibrating with data.
                try:
                    from app.services.analytics_service import AnalyticsService
                    AnalyticsService().record_event(
                        event_type="price_band_clamp", entity_type="task", entity_id=task_id,
                        value=round(float(market_price), 2),
                        payload={"product_format": task_type, "market_p50": round(float(market_price), 2),
                                 "band": [band_lo, band_hi],
                                 "direction": "above" if float(market_price) > band_hi else "below"},
                    )
                    logger.info(f"PipelineOrchestrator: price_band_clamp {task_type} p50={market_price} "
                                f"outside band [{band_lo},{band_hi}]")
                except Exception:
                    pass

            # P0-4: for POD, the cost-based margin-safe price WINS over the band
            # clamp (a sale must never lose money, even if it exceeds the band).
            # Also state the exact variant sold (P0-5 honesty) in the description.
            if is_pod and pod_product is not None:
                pc = getattr(pod_product, "price_cents", None)
                if pc:
                    listing["price"] = round(pc / 100.0, 2)
                vt = getattr(pod_product, "variant_title", None)
                if vt:
                    listing["description"] = (listing.get("description") or "") + \
                        f"\n\nSold as: {vt} (made to order — printed just for you)."
            else:
                # 4-2: charm pricing for digital — snap to a .99/.49 anchor within
                # the band (POD keeps its exact cost-based price to protect margin).
                from app.core.product_formats import snap_charm
                listing["price"] = snap_charm(listing["price"], task_type)

            if is_pod:
                listing["type"] = "physical"
                # P0-5: made-to-order POD goods must not sell out after one sale;
                # 999 keeps the winning listing alive (Etsy marks quantity=1 sold
                # out and deactivates it).
                listing["quantity"] = 999
                svc = EtsyShippingService()
                shipping_id = asyncio.run(svc.get_or_create())
                if shipping_id:
                    listing["shipping_profile_id"] = shipping_id
                # Etsy requires a readiness_state_id on physical listings ("A
                # readiness_state_id is required for physical listings" 400) —
                # prefer the shop's made_to_order state for POD.
                readiness_id = asyncio.run(svc.get_readiness_state_id())
                if readiness_id:
                    listing["readiness_state_id"] = readiness_id
            else:
                listing["type"] = "download"
                listing["quantity"] = 999  # unlimited digital supply

            draft = asyncio.run(EtsyClient().create_draft_listing(listing))
            listing_id = str(draft.get("listing_id", ""))
            if not listing_id:
                raise RuntimeError(f"Etsy API returned no listing_id: {draft}")

            # C-5: Etsy's $0.20 listing fee is real same-day money — record it in
            # the ledger (P0-13 only covered image/vision spend).
            try:
                from app.services.autonomy_service import AutonomyService
                AutonomyService().record_spend(0.20, f"etsy listing fee {listing_id}")
            except Exception:
                pass

            report["stages"]["create_listing"] = {"ok": True, "listing_id": listing_id}
        except Exception as e:
            logger.error(f"PipelineOrchestrator: create_listing failed for {task_id}: {e}")
            self._alert("Etsy listing creation failed", f"task_id={task_id}: {e}")
            report["stages"]["create_listing"] = {"ok": False, "error": str(e)}
            return None

        # Readback verification (steps 93 + 95): confirm the REAL listing's
        # taxonomy_id AND when_made match what was intended, rather than
        # trusting the create response alone. Both had silent, functionally-
        # broken defaults confirmed live on 4534427807: taxonomy_id=1
        # ("Accessories", too broad) and when_made=made_to_order (hides the
        # digital file in Etsy's editor). This also catches Etsy silently
        # storing something other than what was requested.
        try:
            real_listing = asyncio.run(EtsyClient().get_listing(listing_id))
        except Exception as e:
            reason = f"listing readback call failed: {e}"
            report["stages"]["create_listing"] = {"ok": False, "listing_id": listing_id, "error": reason}
            self._cleanup_unbacked_listing(listing_id, report)
            self._block_task(task_id, reason, report, pre_listing=False)
            return None

        real_taxonomy_id = real_listing.get("taxonomy_id")
        if real_taxonomy_id != intended_taxonomy_id:
            reason = f"taxonomy_id mismatch: intended {intended_taxonomy_id}, listing actually has {real_taxonomy_id}"
            report["stages"]["create_listing"] = {"ok": False, "listing_id": listing_id, "error": reason}
            self._cleanup_unbacked_listing(listing_id, report)
            self._block_task(task_id, reason, report, pre_listing=False)
            return None

        real_when_made = real_listing.get("when_made")
        if real_when_made != intended_when_made:
            reason = f"when_made mismatch: intended {intended_when_made}, listing actually has {real_when_made}"
            report["stages"]["create_listing"] = {"ok": False, "listing_id": listing_id, "error": reason}
            self._cleanup_unbacked_listing(listing_id, report)
            self._block_task(task_id, reason, report, pre_listing=False)
            return None

        report["stages"]["create_listing"]["taxonomy_id"] = real_taxonomy_id
        report["stages"]["create_listing"]["when_made"] = real_when_made
        # 5-1: persist the verified listing_id NOW so a crash before the final
        # COMPLETED stamp can't make resume create a duplicate listing.
        self.task_service.record_created_listing(task_id, listing_id)
        return listing_id

    def _stage_attach_publish(self, task_id: str, listing_id: str, image_paths: list, design_path: Optional[Path], digital_required: bool, report: dict, digital_files: Optional[list] = None, alt_text_base: str = None):
        try:
            files = [str(p) for p in digital_files] if digital_files else ([str(design_path)] if design_path else [])
            result = asyncio.run(
                EtsyImageService().attach_images_and_publish(
                    listing_id=listing_id,
                    listing_image_paths=[str(p) for p in image_paths],
                    digital_file_path=files[0] if files else None,  # back-compat
                    digital_file_paths=files,
                    alt_text_base=alt_text_base,
                )
            )

            digital_upload = result.get("digital_upload")
            digital_upload_failed = digital_required and (not digital_upload or "error" in digital_upload)
            if digital_upload_failed:
                reason = digital_upload.get("error") if digital_upload else "no digital file was uploaded"
                report["stages"]["attach_publish"] = {
                    "ok": False,
                    "images_uploaded": len(result.get("uploaded_images", [])),
                    "error": f"required digital file upload failed: {reason}",
                }
                self._cleanup_unbacked_listing(listing_id, report)
                self._block_task(task_id, f"digital file upload failed: {reason}", report, pre_listing=False)
                return

            # Readback verification: confirm listing photos are REALLY
            # attached (GET from Etsy), not just that the upload call
            # returned success. Applies whenever we attempted at least one
            # listing image (both digital and POD listings use this gallery).
            expected_images = sum(1 for u in result.get("uploaded_images", []) if "error" not in u)
            if expected_images > 0:
                try:
                    actual_images = asyncio.run(EtsyImageService().get_listing_images(listing_id))
                except Exception as e:
                    actual_images = None
                    readback_error = str(e)
                else:
                    readback_error = None

                if actual_images is None or len(actual_images) < expected_images:
                    reason = readback_error or f"readback shows {len(actual_images or [])} image(s), expected at least {expected_images}"
                    report["stages"]["attach_publish"] = {
                        "ok": False,
                        "images_uploaded": len(result.get("uploaded_images", [])),
                        "error": f"listing image readback failed: {reason}",
                    }
                    self._cleanup_unbacked_listing(listing_id, report)
                    self._block_task(task_id, f"listing image readback failed: {reason}", report, pre_listing=False)
                    return

            # Readback verification (step 92): confirm the digital FILE is
            # REALLY attached (GET from Etsy), not just that upload_digital_file()
            # returned success. A listing with photos and no downloadable file
            # is exactly the "worse than no listing" case this gate exists to
            # prevent — production has hit this class of silent-success bug
            # before (publish_listing's endpoint bug), so this can't be
            # assumed away just because the upload call didn't error.
            if digital_required and digital_upload:
                try:
                    actual_files = asyncio.run(EtsyImageService().get_listing_files(listing_id))
                except Exception as e:
                    actual_files = None
                    file_readback_error = str(e)
                else:
                    file_readback_error = None

                if not actual_files:
                    reason = file_readback_error or "readback shows 0 files attached, expected at least 1"
                    report["stages"]["attach_publish"] = {
                        "ok": False,
                        "images_uploaded": len(result.get("uploaded_images", [])),
                        "error": f"digital file readback failed: {reason}",
                    }
                    self._cleanup_unbacked_listing(listing_id, report)
                    self._block_task(task_id, f"digital file readback failed: {reason}", report, pre_listing=False)
                    return

                # Step 93b: a file can be attached (count >= 1) yet stored with
                # an unrecognised content-type (application/octet-stream), in
                # which case Etsy's editor never DISPLAYS it — the file exists
                # but is functionally invisible/unusable. Confirmed live on
                # listing 4534427807. Treat an octet-stream-only file set as a
                # readback failure the same as no file at all: a buyer can't
                # get a file the listing won't surface.
                from app.services.etsy_image_service import GENERIC_BINARY_CONTENT_TYPE
                displayable = [
                    f for f in actual_files
                    if f.get("filetype") and f.get("filetype") != GENERIC_BINARY_CONTENT_TYPE
                ]
                if not displayable:
                    filetypes = [f.get("filetype") for f in actual_files]
                    reason = f"attached file(s) have unrecognised filetype {filetypes} (won't display in Etsy's editor)"
                    report["stages"]["attach_publish"] = {
                        "ok": False,
                        "images_uploaded": len(result.get("uploaded_images", [])),
                        "error": f"digital file readback failed: {reason}",
                    }
                    self._cleanup_unbacked_listing(listing_id, report)
                    self._block_task(task_id, f"digital file readback failed: {reason}", report, pre_listing=False)
                    return

            # Publish-state verification (step 92): a 200 OK from Etsy's
            # publish PATCH does NOT guarantee the listing actually
            # transitioned to "active" — confirmed live in production (task
            # fb66a81a / listing 4534427807): the PATCH returned 200 but the
            # listing stayed in "edit" (draft, invisible to buyers) due to a
            # propagation lag. EtsyImageService.publish_listing() now checks
            # the response body's real state and retries once, so
            # publish_result["published"] reflects ground truth, not just
            # "the HTTP call didn't error". Only treated as a blocking
            # failure when we actually intended to go live.
            publish_result = result.get("publish_result") or {}
            intended_to_publish = "reason" not in publish_result  # AUTO_PUBLISH_LISTINGS=False sets "reason"
            if intended_to_publish and not publish_result.get("published"):
                reason = f"listing state is {publish_result.get('state', 'unknown')!r}, not 'active'"
                report["stages"]["attach_publish"] = {
                    "ok": False,
                    "images_uploaded": len(result.get("uploaded_images", [])),
                    "error": f"publish did not take effect: {reason}",
                }
                self._cleanup_unbacked_listing(listing_id, report)
                self._block_task(task_id, f"publish did not take effect: {reason}", report, pre_listing=False)
                return

            # Persist the real, readback-verified listing_id onto this task's
            # catalog assets. This is the durable "genuinely published product"
            # signal (blocked tasks never reach here), used by the marketing-
            # refresh system to find real products to re-promote. Best-effort:
            # a catalog write must never fail an otherwise-successful publish.
            try:
                if design_path:
                    self.catalog.attach_listing(str(design_path), listing_id)
                for p in (image_paths or []):
                    self.catalog.attach_listing(str(p), listing_id)
            except Exception as link_err:
                logger.warning(f"PipelineOrchestrator: could not attach listing_id {listing_id} to catalog assets: {link_err}")

            report["stages"]["attach_publish"] = {
                "ok": True,
                "images_uploaded": len(result.get("uploaded_images", [])),
                "published": publish_result.get("published", False),
            }
        except Exception as e:
            logger.error(f"PipelineOrchestrator: attach_publish failed for listing {listing_id}: {e}")
            self._alert("Etsy image attach/publish failed", f"listing_id={listing_id}: {e}")
            report["stages"]["attach_publish"] = {"ok": False, "error": str(e)}
            if digital_required:
                self._cleanup_unbacked_listing(listing_id, report)
                self._block_task(task_id, f"attach/publish failed for required digital product: {e}", report, pre_listing=False)

    # 5-4: derived marketing assets (pin.png / video.mp4) can land back in the
    # catalog on a re-run/resume; picking the FIRST asset could then feed a pin
    # BACK into itself. Only ever source from a real listing PHOTO of the design.
    _DERIVED_ASSET_NAMES = ("pin.png", "video.mp4", "set_triptych.png")

    def _mockup_source(self, task_id: str) -> Optional[str]:
        """The best existing listing PHOTO for this task (shared by the pin and
        the video). Filters to use_case=='listing' and excludes derived marketing
        assets so a re-run can't source a pin/video from a previous pin/video."""
        assets = self.catalog.get_listing_assets(task_id) or []
        for a in assets:
            lp = a.local_path
            if not lp or not Path(lp).exists():
                continue
            if getattr(a, "use_case", None) not in (None, "listing"):
                continue
            if Path(lp).name in self._DERIVED_ASSET_NAMES:
                continue
            return lp
        return None

    def _pin_from_mockup(self, task_id: str) -> Optional[str]:
        """3-3: render a free 2:3 (1000x1500) Pinterest pin from an existing
        listing mockup of the real design. Returns a path, or None if no mockup."""
        try:
            from PIL import Image, ImageOps
            from app.services.image_file_service import ImageFileService
            from io import BytesIO
            src = self._mockup_source(task_id)  # 5-4: real listing photo only
            if not src:
                return None
            img = Image.open(src).convert("RGB")
            pin = ImageOps.pad(img, (1000, 1500), color=(255, 255, 255), method=Image.LANCZOS)
            buf = BytesIO()
            pin.save(buf, format="PNG")
            return str(ImageFileService().save_bytes(buf.getvalue(), task_id, "listing", "pin.png"))
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: pin-from-mockup failed for {task_id}: {e}")
            return None

    def _stage_listing_video(self, task_id: str, listing_id: str, report: dict):
        """3-4: render a ken-burns MP4 from the verified design and upload it as
        the listing video. Gated by LISTING_VIDEO_ENABLED (off by default —
        encoding adds CPU/time to every publish; flip it on when ready). Fully
        best-effort: never fails a publish, never spends money."""
        from config import settings
        if not getattr(settings, "LISTING_VIDEO_ENABLED", False):
            report["stages"]["listing_video"] = {"skipped": "LISTING_VIDEO_ENABLED off"}
            return
        try:
            src = self._mockup_source(task_id)
            if not src:
                report["stages"]["listing_video"] = {"skipped": "no mockup source"}
                return
            from app.services.listing_video_service import ListingVideoService
            from app.services.image_file_service import ImageFileService
            ifs = ImageFileService()
            ifs.listing_dir(task_id).mkdir(parents=True, exist_ok=True)
            out_path = str(ifs.listing_dir(task_id) / "video.mp4")
            ListingVideoService().render(src, out_path)
            import asyncio
            asyncio.run(EtsyImageService().upload_listing_video(listing_id, out_path))
            report["stages"]["listing_video"] = {"uploaded": True, "path": out_path}
            logger.info(f"PipelineOrchestrator: listing video uploaded for {listing_id}")
        except Exception as e:
            logger.warning(f"PipelineOrchestrator: listing video failed for {task_id}: {e}")
            report["stages"]["listing_video"] = {"error": str(e)[:200]}

    def _stage_pinterest(self, task_id: str, product_name: str, visual_brief: str, output_data: dict, report: dict, task_type: str = None, listing_id: Optional[str] = None):
        from config import settings

        try:
            # P0-6 / P1-5 (#1c,#5): skip BEFORE generating the pin image when
            # Pinterest can't receive a post anyway. is_connected() only proves an
            # OAuth token exists; a Trial-access app is "connected" yet 403s every
            # pin-create (code 29), so we'd pay ~$0.04 for an image thrown away on
            # every task. can_publish() additionally checks real publish capability
            # (auto-detected from post history / operator override) and auto-resumes
            # once Standard access lands.
            from app.services.pinterest_oauth import can_publish as pinterest_can_publish
            if not pinterest_can_publish():
                report["stages"]["pinterest"] = {"skipped": "Pinterest cannot publish (not connected or Trial-blocked)"}
                return

            marketing_svc = MarketingService()
            existing = marketing_svc.get_posts_for_task(task_id)
            if any(p.channel == "pinterest" and p.status == "success" for p in existing):
                report["stages"]["pinterest"] = {"skipped": "already posted successfully"}
                return

            listing = {
                "title": output_data.get("title", ""),
                "description": output_data.get("description", ""),
                "keywords": output_data.get("keywords", []),
                "product_name": product_name,
                "product_format": task_type,  # A-9: per-format board routing
                # CRITICAL: the pin MUST link back to the Etsy listing or it drives
                # zero traffic (a linkless pin is a dead end). This was missing —
                # every main-pipeline pin had an empty link, so 50+ pins/week sent
                # nobody to the shop. Mirrors _stage_tumblr's listing_url.
                "listing_url": f"https://www.etsy.com/listing/{listing_id}" if listing_id else "",
            }

            # 3-3: compose the pin from an EXISTING free listing mockup of the
            # real design (Pinterest's ideal is 2:3) instead of spending $0.04 on
            # a fresh generation — which also reintroduces the "marketing image
            # differs from the product" risk. Fall back to generation only if no
            # mockup exists.
            pin_path_str = self._pin_from_mockup(task_id)
            if pin_path_str:
                import base64 as _b64
                with open(pin_path_str, "rb") as _f:
                    listing["image_base64"] = _b64.b64encode(_f.read()).decode()
                listing["pin_image_path"] = pin_path_str
                enriched = listing
                report["stages"].setdefault("pinterest_pin", {"source": "mockup"})
            else:
                enriched = PinterestImageService().enrich_listing_with_image(
                    listing=listing, task_id=task_id, visual_brief=visual_brief,
                )
                pin_path_str = enriched.get("pin_image_path")
                report["stages"].setdefault("pinterest_pin", {"source": "generated"})

            # Register pin image in catalog
            if pin_path_str:
                self.catalog.register(
                    task_id=task_id,
                    local_path=pin_path_str,
                    variant="listing",
                    use_case="pinterest",
                    agent="PinterestPinMockup" if report["stages"].get("pinterest_pin", {}).get("source") == "mockup" else "SocialImageAgent",
                    provider=settings.IMAGE_PROVIDER,
                    model=settings.OPENROUTER_IMAGE_MODEL,
                )

            result = marketing_svc.post_to_channel(
                task_id=task_id,
                listing=enriched,
                channel=PinterestChannel(),
            )
            report["stages"]["pinterest"] = {"ok": True, "success": result.get("success", False)}
        except Exception as e:
            logger.error(f"PipelineOrchestrator: pinterest failed for {task_id}: {e}")
            self._alert("Pinterest post failed", f"task_id={task_id}: {e}")
            report["stages"]["pinterest"] = {"ok": False, "error": str(e)}

    def _stage_tumblr(self, task_id: str, product_name: str, output_data: dict, listing_id: Optional[str], report: dict):
        """Post the new listing to Tumblr (step 100i). Previously the pipeline
        only posted to Pinterest, so a newly-created listing never got a Tumblr
        post — Tumblr was only ever posted by the recurring marketing-refresh
        worker (default OFF), so in practice new products were never shared on
        Tumblr. This posts on creation using the attractive scene-composited
        listing mockup (never the raw deliverable) + the Etsy listing URL. Best-effort;
        skipped cleanly when Tumblr isn't configured/connected."""
        from config import settings

        try:
            if not getattr(settings, "TUMBLR_CONSUMER_KEY", None):
                report["stages"]["tumblr"] = {"skipped": "Tumblr not configured"}
                return
            from app.db.database import SessionLocal
            from app.models.tumblr_token import TumblrToken
            db = SessionLocal()
            try:
                connected = db.query(TumblrToken).first() is not None
            finally:
                db.close()
            if not connected:
                report["stages"]["tumblr"] = {"skipped": "Tumblr not connected"}
                return

            marketing_svc = MarketingService()
            existing = marketing_svc.get_posts_for_task(task_id)
            if any(p.channel == "tumblr" and p.status == "success" for p in existing):
                report["stages"]["tumblr"] = {"skipped": "already posted successfully"}
                return

            # An attractive scene-composited listing photo — NEVER the raw delivery.
            asset_path = None
            for a in self.catalog.get_listing_assets(task_id):
                if a.use_case == "listing" and Path(a.local_path).exists():
                    asset_path = a.local_path
                    break
            if not asset_path:
                report["stages"]["tumblr"] = {"skipped": "no listing image asset to post"}
                return

            listing = {
                "title": output_data.get("title", "") or product_name,
                "description": output_data.get("description", ""),
                "keywords": output_data.get("keywords", []),
                "image_path": asset_path,
                "listing_url": f"https://www.etsy.com/listing/{listing_id}" if listing_id else "",
            }
            from app.marketing.tumblr_channel import TumblrChannel
            result = marketing_svc.post_to_channel(task_id=task_id, listing=listing, channel=TumblrChannel())
            report["stages"]["tumblr"] = {"ok": True, "success": result.get("success", False), "error": result.get("error")}
        except Exception as e:
            logger.error(f"PipelineOrchestrator: tumblr failed for {task_id}: {e}")
            self._alert("Tumblr post failed", f"task_id={task_id}: {e}")
            report["stages"]["tumblr"] = {"ok": False, "error": str(e)}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _alert(self, title: str, message: str):
        try:
            from app.services.alert_service import AlertService  # noqa: keep lazy to avoid circular at import time
            AlertService().send_alert_sync(
                f"PipelineOrchestrator: {title}", message, level="error"
            )
        except Exception:
            pass
