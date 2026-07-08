"""
Post-completion pipeline orchestrator.

Called by TaskProcessor immediately after a task transitions to DONE.
Chains image generation → hard product gate → Etsy listing creation → image
attachment / publish → Pinterest marketing → POD Printify product.

Each stage runs in its own try/except — one failure does NOT prevent
subsequent stages from being attempted, EXCEPT for the hard product gate
(step 90): for 'digital_download' and 'pod' tasks, a verified real product
artifact MUST exist before EtsyClient.create_draft_listing() is ever called.
A listing with nothing real behind it is worse than no listing — so any
precondition failure blocks listing creation entirely rather than degrading
to a best-effort attempt. All failures are logged and alerted via the
existing AlertService pattern.

Stage order:
  1. listing_images    — ProductImageAgent hero + lifestyle (always)
  2. pod_design         — PODPipelineService delivery asset (pod/digital_download)
  3. printify_precheck  — PODFulfillmentService.create_product_for_task (pod only;
                           runs BEFORE listing creation so a failure blocks it)
  4. HARD GATE          — delivery asset must exist + pass validation (pod/digital);
                           Printify product must exist (pod). Any failure: task is
                           marked BLOCKED_NO_PRODUCT and create_draft_listing is
                           never called.
  5. create_listing     — Etsy draft listing via EtsyClient (always, if gate passed)
  6. attach_publish     — EtsyImageService.attach_images_and_publish (needs listing_id).
                           For digital products, a failed/missing digital file upload
                           is also treated as a gate failure: the draft listing is
                           deleted and the task is blocked.
  7. printify_link      — link the precreated Printify product to the real listing_id
  8. pinterest          — SocialImageAgent + MarketingService (independent of Etsy)
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
from app.services.etsy_client import EtsyClient
from app.services.etsy_image_service import EtsyImageService
from app.services.pod_fulfillment_service import PODFulfillmentService
from app.services.pinterest_image_service import PinterestImageService
from app.services.marketing_service import MarketingService
from app.marketing.pinterest_channel import PinterestChannel

logger = logging.getLogger("ai-factory")

DIGITAL_DOWNLOAD_TYPE = "digital_download"
POD_TYPE = "pod"
DELIVERY_ASSET_TASK_TYPES = {POD_TYPE, DIGITAL_DOWNLOAD_TYPE}


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

        output_data = task.output_data or {}
        product_name = (output_data.get("title") or task.prompt or "Product")[:140]
        visual_brief = output_data.get("description") or task.prompt or ""
        task_type = task.type or "general"
        is_pod = task_type == POD_TYPE
        is_digital = task_type == DIGITAL_DOWNLOAD_TYPE
        needs_delivery_asset = task_type in DELIVERY_ASSET_TASK_TYPES

        is_autonomy = bool((task.metadata_ or {}).get("source") == "autonomy_worker")
        report: dict = {"task_id": task_id, "task_type": task_type, "stages": {}}

        # 1 — listing images
        image_paths = self._stage_listing_images(task_id, product_name, visual_brief, is_autonomy, report)

        # 2 — delivery asset (POD design / digital download file)
        design_path = None
        if needs_delivery_asset:
            design_path = self._stage_pod_design(task_id, product_name, visual_brief, task_type, report)

        # 3 — POD physical: a real Printify product must exist BEFORE a listing
        # is created, so its failure can block listing creation outright.
        pod_product = None
        if is_pod:
            pod_product = self._stage_printify_precheck(task_id, report)

        # 4 — HARD GATE: no listing without a verified real product behind it.
        if needs_delivery_asset:
            gate_error = self._delivery_gate_error(task_id, is_pod, pod_product)
            if gate_error:
                self._block_task(task_id, gate_error, report, pre_listing=True)
                return report

        # 5 — create Etsy draft listing
        listing_id = self._stage_create_listing(task_id, product_name, output_data, task_type, is_pod, report)

        # 6 — attach images / digital file, then publish
        if listing_id:
            self._stage_attach_publish(task_id, listing_id, image_paths, design_path, is_digital, report)
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

    def _delivery_gate_error(self, task_id: str, is_pod: bool, pod_product) -> Optional[str]:
        """
        Returns a human-readable blocking reason if the required real product
        preconditions are not met, or None if the gate is satisfied.
        """
        asset = self.catalog.get_delivery_asset(task_id)
        if not asset or not Path(asset.local_path).exists():
            return "no verified delivery asset — image generation or validation failed"
        if is_pod and not pod_product:
            return "Printify product creation failed — no real POD product exists"
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
        real product behind it. If deletion itself fails, alert so Maj can
        remove it manually from Etsy's Shop Manager UI.
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

    # ── Stages ────────────────────────────────────────────────────────────────

    def _stage_listing_images(self, task_id: str, product_name: str, visual_brief: str, is_autonomy: bool, report: dict) -> list:
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
            if is_autonomy and saved_paths:
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

        try:
            result = PODPipelineService().build_product_record(
                task_id=task_id,
                product_name=product_name,
                visual_brief=visual_brief,
                product_type=task_type,
            )
            design_str = result.get("design_path")
            if not design_str:
                report["stages"]["pod_design"] = {"ok": False, "error": "no design artifact was generated"}
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
                logger.warning(f"PipelineOrchestrator: POD design failed validation: {ve}")
                report["stages"]["pod_design"] = {"ok": False, "error": f"validation failed: {ve}"}
                return None

            report["stages"]["pod_design"] = {"ok": True, "design_path": design_str}
            return design_path
        except Exception as e:
            logger.error(f"PipelineOrchestrator: pod_design failed for {task_id}: {e}")
            self._alert("POD design generation failed", f"task_id={task_id}: {e}")
            report["stages"]["pod_design"] = {"ok": False, "error": str(e)}
            return None

    def _stage_printify_precheck(self, task_id: str, report: dict):
        """
        Create the real Printify product BEFORE any Etsy listing exists, so a
        failure here can block listing creation outright (per the hard gate).
        Does not pass etsy_listing_id yet — _stage_link_printify_listing wires
        it up once a real listing_id exists.
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

        try:
            product = {
                "product_name": product_name,
                "concept": product_name,
                "materials": [],
                "estimated_price_range": "$10-25",
                "target_audience": "",
            }
            listing = ListingGeneratorAgent().generate_listing(product, output_data)

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
            return listing_id
        except Exception as e:
            logger.error(f"PipelineOrchestrator: create_listing failed for {task_id}: {e}")
            self._alert("Etsy listing creation failed", f"task_id={task_id}: {e}")
            report["stages"]["create_listing"] = {"ok": False, "error": str(e)}
            return None

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

            report["stages"]["attach_publish"] = {
                "ok": True,
                "images_uploaded": len(result.get("uploaded_images", [])),
                "published": result.get("publish_result", {}).get("published", False),
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
