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
        digital_required = format_spec["category"] == "digital"

        is_autonomy = bool((task.metadata_ or {}).get("source") == "autonomy_worker")

        # 1 — listing images
        image_paths = self._stage_listing_images(task_id, product_name, visual_brief, is_autonomy, report)

        # 2 — delivery asset (single image or multi-page PDF)
        if is_pdf:
            design_path = self._stage_pdf_design(task, task_id, product_name, visual_brief, output_data, report)
        else:
            design_path = self._stage_pod_design(task_id, product_name, visual_brief, task_type, report)

        # 2.5 — CONTENT-QUALITY GATE (step 96). The FIRST check that inspects
        # actual content, not structure — catches garbled/incoherent
        # image-model text (e.g. "2 þutter") that every structural gate missed.
        # Runs BEFORE anything is uploaded to Etsy/Printify: no point
        # taxonomy-checking or uploading content already known to be garbage.
        # PDFs are content-QA'd per-page inside PDFGenerationService during
        # generation, so only single-image formats are reviewed here.
        if design_path and not is_pdf:
            design_path = self._stage_content_quality(task_id, design_path, product_name, visual_brief, task_type, report)
            if report.get("blocked"):
                return report

        # 2.6 — For DIGITAL single-image products, the honest primary listing
        # photo IS the (now content-verified) delivered design — not an
        # independently-generated generic mockup that can depict something
        # completely different (the misrepresentation Maj found). Prepend it so
        # Etsy's featured photo shows exactly what the buyer receives.
        if design_path and digital_required and not is_pdf:
            if design_path not in image_paths:
                # Just prepend for upload ordering — do NOT re-register it in
                # the catalog under a "listing" variant: register is
                # idempotent-on-path and would clobber the "delivery" record
                # the hard gate relies on (get_delivery_asset).
                image_paths = [design_path] + list(image_paths)

        # 2.7 — MARKETING/DELIVERABLE CONSISTENCY GATE (step 96): backstop that
        # every listing photo plausibly depicts the same product as the
        # delivery asset, blocking a buyer-misrepresentation before listing.
        if design_path and image_paths:
            image_paths = self._stage_marketing_consistency(
                task_id, design_path, image_paths, product_name, visual_brief, is_autonomy, report
            )
            if report.get("blocked"):
                return report

        # 3 — POD physical: a real Printify product must exist BEFORE a listing
        # is created, so its failure can block listing creation outright.
        pod_product = None
        if is_pod:
            pod_product = self._stage_printify_precheck(task_id, report)

        # 4 — HARD GATE: no listing without a verified real product behind it.
        gate_error = self._delivery_gate_error(task_id, is_pdf, is_pod, pod_product)
        if gate_error:
            self._block_task(task_id, gate_error, report, pre_listing=True)
            return report

        # 5 — create Etsy draft listing
        listing_id = self._stage_create_listing(task_id, product_name, output_data, task_type, is_pod, report)

        # 6 — attach images / digital file, then publish; readback-verify both
        if listing_id:
            self._stage_attach_publish(task_id, listing_id, image_paths, design_path, digital_required, report)
        else:
            report["stages"]["attach_publish"] = {"skipped": "create_listing failed"}

        if report.get("blocked"):
            return report

        # 7 — link the precreated Printify product to the now-real listing
        if is_pod and listing_id and pod_product:
            self._stage_link_printify_listing(pod_product, listing_id, report)

        # 8 — Pinterest (independent of Etsy stages)
        self._stage_pinterest(task_id, product_name, visual_brief, output_data, report)

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

    def _stage_content_quality(self, task_id, design_path, product_name, visual_brief, task_type, report):
        """
        Vision-model review of the delivered single-image asset for legible,
        coherent, correct, sellable content. On failure, REGENERATE the
        delivery asset and re-review up to CONTENT_QA_MAX_ATTEMPTS times, then
        block the task (same as every other gate failure). Returns the
        content-verified design path, or None if blocked.
        """
        from config import settings
        from app.services.content_quality_service import ContentQualityService

        svc = ContentQualityService()
        attempts = max(1, settings.CONTENT_QA_MAX_ATTEMPTS)
        current = design_path
        last_issues = ["content quality check did not pass"]

        for attempt in range(1, attempts + 1):
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
                current = self._stage_pod_design(task_id, product_name, visual_brief, task_type, report)
                if not current:
                    break

        reason = f"content quality gate failed: {'; '.join(last_issues)[:300]}"
        report["stages"]["content_quality"] = {"ok": False, "error": reason, "issues": last_issues}
        self._block_task(task_id, reason, report, pre_listing=True)
        return None

    def _stage_marketing_consistency(self, task_id, design_path, image_paths, product_name, visual_brief, is_autonomy, report):
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

        for remake in range(1, max_remakes + 1):
            if result and result.passed:
                break

            targets = self._resolve_mismatch_targets(current_paths, design_path, result)
            if not targets:
                # No marketing image we can safely regenerate (e.g. the only
                # flagged image IS the delivery asset, or no structured
                # breakdown and nothing but the delivery asset present).
                break

            logger.warning(
                f"PipelineOrchestrator: marketing/deliverable mismatch for {task_id} "
                f"(remake {remake}/{max_remakes}); regenerating marketing image indices "
                f"{sorted(targets)} with corrective feedback"
            )

            regenerated_any = False
            for idx, issue in targets.items():
                new_path = self._regenerate_marketing_image(
                    task_id, product_name, visual_brief, current_paths[idx],
                    corrective_issue=issue, ground_truth=ground_truth, report=report,
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
            report["stages"]["marketing_consistency"] = {"ok": True, "remakes": remakes_used}
            return current_paths

        reason = f"marketing/deliverable mismatch: {'; '.join(last_issues)[:300]}"
        report["stages"]["marketing_consistency"] = {
            "ok": False, "error": reason, "issues": last_issues, "remakes": remakes_used,
        }
        self._block_task(task_id, reason, report, pre_listing=True)
        return None

    def _consistency_issues(self, result):
        """Human-readable issue list from a consistency result (or a vision error)."""
        if result is None:
            return ["marketing consistency check failed (vision call error)"]
        return result.specific_issues or ["marketing images do not match the delivered product"]

    def _resolve_mismatch_targets(self, current_paths, design_path, result):
        """
        Map the vision model's per-image mismatch reports to indices into
        current_paths, returning {index: issue_text}. The delivery asset is
        NEVER a target — it's confirmed correct. When the model returned no
        structured per-image breakdown (older schema / unparseable), fall back
        to every non-delivery marketing image so a remake can still be attempted.
        """
        design_str = str(design_path)
        # marketing images are numbered 1..N over the images that actually exist,
        # in list order — matching how check_marketing_consistency sends them.
        existing = [(i, p) for i, p in enumerate(current_paths) if Path(p).exists()]
        targets: dict = {}

        mismatches = (result.mismatches if result else None) or []
        if mismatches:
            for m in mismatches:
                j = m.get("image_index")
                if not isinstance(j, int) or not (1 <= j <= len(existing)):
                    continue
                orig_idx, p = existing[j - 1]
                if str(p) == design_str:
                    continue  # never regenerate the delivery asset
                targets[orig_idx] = m.get("issue") or "does not match the delivered design"
            return targets

        generic = "; ".join(result.specific_issues) if (result and result.specific_issues) else "does not match the delivered design"
        for orig_idx, p in existing:
            if str(p) == design_str:
                continue
            targets[orig_idx] = generic
        return targets

    def _regenerate_marketing_image(self, task_id, product_name, visual_brief, target_path, corrective_issue, ground_truth, report):
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
        name = target_path.name.lower()
        slot = "lifestyle" if "lifestyle" in name else "hero"
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
                slot=slot,
                corrective_guidance=corrective_guidance,
                filename=target_path.name,
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

    def _stage_listing_images(self, task_id: str, product_name: str, visual_brief: str, is_autonomy: bool, report: dict, record_spend: bool = True) -> list:
        from config import settings

        saved_paths = []
        try:
            agent = ProductImageAgent()
            result = agent.generate_listing_images(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
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

            # Record image-gen cost for autonomy tasks — worker only charged $0.05
            # upfront for the text-LLM; record the remaining $0.20 here on success.
            # record_spend=False on consistency-driven regeneration avoids
            # double-charging for the same product's listing images.
            if is_autonomy and saved_paths and record_spend:
                try:
                    from app.services.autonomy_service import AutonomyService
                    AutonomyService().record_spend(0.20, f"images generated task={task_id[:8]}")
                except Exception as spend_err:
                    logger.warning(f"PipelineOrchestrator: failed to record autonomy image spend: {spend_err}")

        except Exception as e:
            logger.error(f"PipelineOrchestrator: listing_images failed for {task_id}: {e}")
            self._alert("Listing image generation failed", f"task_id={task_id}: {e}")
            report["stages"]["listing_images"] = {"ok": False, "error": str(e)}

        return saved_paths

    def _stage_pod_design(self, task_id: str, product_name: str, visual_brief: str, task_type: str, report: dict) -> Optional[Path]:
        from config import settings

        # PODPipelineService/PODDesignAgent's product_type label only affects
        # the generation prompt wording — map our format name to the
        # digital/pod label they already understand rather than touching them.
        mapped_type = "pod" if PRODUCT_FORMATS.get(task_type, {}).get("category") == "pod" else "digital_download"

        try:
            result = PODPipelineService().build_product_record(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
                product_type=mapped_type,
            )
            design_str = result.get("design_path")
            if not design_str:
                report["stages"]["delivery_asset"] = {"ok": False, "error": "no design artifact was generated"}
                return None

            design_path = Path(design_str)
            try:
                ImageValidationService().validate(design_path, use_case="delivery")
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

    def _stage_pdf_design(self, task, task_id: str, product_name: str, visual_brief: str, output_data: dict, report: dict) -> Optional[Path]:
        from config import settings

        page_briefs = self._resolve_pdf_page_briefs(task, output_data)

        try:
            pdf_path = PDFGenerationService().generate_pdf(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
                page_briefs=page_briefs,
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

    def _stage_printify_precheck(self, task_id: str, report: dict):
        """
        Create the real Printify product BEFORE any Etsy listing exists, so a
        failure here can block listing creation outright (per the hard gate).
        PODFulfillmentService.create_product_for_task() already re-fetches
        the product from Printify to confirm the image is really attached.
        Does not pass etsy_listing_id yet — _stage_link_printify_listing
        wires it up once a real listing_id exists.
        """
        try:
            pod = PODFulfillmentService().create_product_for_task(task_id)
            report["stages"]["printify_product"] = {"ok": True, "pod_product_id": pod.id}
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

    def _stage_create_listing(self, task_id: str, product_name: str, output_data: dict, task_type: str, is_pod: bool, report: dict) -> Optional[str]:
        from app.services.etsy_shipping_service import EtsyShippingService
        from app.services.etsy_client import DIGITAL_WHEN_MADE, POD_WHEN_MADE

        intended_taxonomy_id = PRODUCT_FORMATS.get(task_type, {}).get("taxonomy_id", 1)
        # Digital downloads must NOT be made_to_order or Etsy hides the
        # instant-download file slot in its editor (confirmed live on
        # 4534427807). made_to_order is correct for POD physical goods.
        intended_when_made = POD_WHEN_MADE if is_pod else DIGITAL_WHEN_MADE

        try:
            product = {
                "product_name": product_name,
                "concept": product_name,
                "materials": [],
                "estimated_price_range": "$10-25",
                "target_audience": "",
            }
            listing = ListingGeneratorAgent().generate_listing(product, output_data)
            listing["taxonomy_id"] = intended_taxonomy_id
            listing["when_made"] = intended_when_made

            if is_pod:
                listing["type"] = "physical"
                listing["quantity"] = 1
                shipping_id = asyncio.run(EtsyShippingService().get_or_create())
                if shipping_id:
                    listing["shipping_profile_id"] = shipping_id
            else:
                listing["type"] = "download"
                listing["quantity"] = 999  # unlimited digital supply

            draft = asyncio.run(EtsyClient().create_draft_listing(listing))
            listing_id = str(draft.get("listing_id", ""))
            if not listing_id:
                raise RuntimeError(f"Etsy API returned no listing_id: {draft}")

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
        return listing_id

    def _stage_attach_publish(self, task_id: str, listing_id: str, image_paths: list, design_path: Optional[Path], digital_required: bool, report: dict):
        try:
            result = asyncio.run(
                EtsyImageService().attach_images_and_publish(
                    listing_id=listing_id,
                    listing_image_paths=[str(p) for p in image_paths],
                    digital_file_path=str(design_path) if design_path else None,
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

    def _stage_pinterest(self, task_id: str, product_name: str, visual_brief: str, output_data: dict, report: dict):
        from config import settings

        try:
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
            }

            enriched = PinterestImageService().enrich_listing_with_image(
                listing=listing,
                task_id=task_id,
                visual_brief=visual_brief,
            )

            # Register pin image in catalog
            pin_path_str = enriched.get("pin_image_path")
            if pin_path_str:
                self.catalog.register(
                    task_id=task_id,
                    local_path=pin_path_str,
                    variant="listing",
                    use_case="pinterest",
                    agent="SocialImageAgent",
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

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _alert(self, title: str, message: str):
        try:
            from app.services.alert_service import AlertService  # noqa: keep lazy to avoid circular at import time
            AlertService().send_alert_sync(
                f"PipelineOrchestrator: {title}", message, level="error"
            )
        except Exception:
            pass
