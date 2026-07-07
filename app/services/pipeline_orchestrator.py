"""
Post-completion pipeline orchestrator.

Called by TaskProcessor immediately after a task transitions to DONE.
Chains image generation → Etsy listing creation → image attachment /
publish → Pinterest marketing → POD Printify product.

Each stage runs in its own try/except — one failure does NOT prevent
subsequent stages from being attempted, unless the failed stage is a
hard prerequisite (create_etsy_listing must succeed before
attach_images_and_publish can run). All failures are logged and alerted
via the existing AlertService pattern.

Stage order:
  1. listing_images    — ProductImageAgent hero + lifestyle (always)
  2. pod_design        — PODPipelineService design asset (pod/digital_download)
  3. create_listing    — Etsy draft listing via EtsyClient (always)
  4. attach_publish    — EtsyImageService.attach_images_and_publish (needs listing_id)
  5. printify_product  — PODFulfillmentService.create_product_for_task (pod only)
  6. pinterest         — SocialImageAgent + MarketingService (always)
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

POD_TASK_TYPES = {"pod", "digital_download"}


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
        is_pod = task_type in POD_TASK_TYPES

        report: dict = {"task_id": task_id, "task_type": task_type, "stages": {}}

        # 1 — listing images
        image_paths = self._stage_listing_images(task_id, product_name, visual_brief, report)

        # 2 — POD design (if applicable)
        design_path = None
        if is_pod:
            design_path = self._stage_pod_design(task_id, product_name, visual_brief, task_type, report)

        # 3 — create Etsy draft listing
        listing_id = self._stage_create_listing(task_id, product_name, output_data, report)

        # 4 — attach images and publish (only if listing was created)
        if listing_id:
            self._stage_attach_publish(listing_id, image_paths, design_path if is_pod else None, report)
        else:
            report["stages"]["attach_publish"] = {"skipped": "create_listing failed"}

        # 5 — Printify product (pod only, needs listing_id)
        if is_pod and listing_id:
            self._stage_printify_product(task_id, listing_id, report)

        # 6 — Pinterest (independent of Etsy stages)
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

    # ── Stages ────────────────────────────────────────────────────────────────

    def _stage_listing_images(self, task_id: str, product_name: str, visual_brief: str, report: dict) -> list:
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
                report["stages"]["pod_design"] = {"ok": True, "skipped": "unsupported type"}
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
                design_path = None

            report["stages"]["pod_design"] = {"ok": True, "design_path": design_str}
            return design_path
        except Exception as e:
            logger.error(f"PipelineOrchestrator: pod_design failed for {task_id}: {e}")
            self._alert("POD design generation failed", f"task_id={task_id}: {e}")
            report["stages"]["pod_design"] = {"ok": False, "error": str(e)}
            return None

    def _stage_create_listing(self, task_id: str, product_name: str, output_data: dict, report: dict) -> Optional[str]:
        try:
            product = {
                "product_name": product_name,
                "concept": product_name,
                "materials": [],
                "estimated_price_range": "$10-25",
                "target_audience": "",
            }
            listing = ListingGeneratorAgent().generate_listing(product, output_data)
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

    def _stage_attach_publish(self, listing_id: str, image_paths: list, design_path: Optional[Path], report: dict):
        try:
            result = asyncio.run(
                EtsyImageService().attach_images_and_publish(
                    listing_id=listing_id,
                    listing_image_paths=[str(p) for p in image_paths],
                    digital_file_path=str(design_path) if design_path else None,
                )
            )
            report["stages"]["attach_publish"] = {
                "ok": True,
                "images_uploaded": len(result.get("uploaded_images", [])),
                "published": result.get("publish_result", {}).get("published", False),
            }
        except Exception as e:
            logger.error(f"PipelineOrchestrator: attach_publish failed for listing {listing_id}: {e}")
            self._alert("Etsy image attach/publish failed", f"listing_id={listing_id}: {e}")
            report["stages"]["attach_publish"] = {"ok": False, "error": str(e)}

    def _stage_printify_product(self, task_id: str, listing_id: str, report: dict):
        try:
            pod = PODFulfillmentService().create_product_for_task(task_id, etsy_listing_id=listing_id)
            report["stages"]["printify_product"] = {
                "ok": True,
                "pod_product_id": pod.id if pod else None,
            }
        except Exception as e:
            logger.error(f"PipelineOrchestrator: printify_product failed for {task_id}: {e}")
            self._alert("Printify product creation failed", f"task_id={task_id}: {e}")
            report["stages"]["printify_product"] = {"ok": False, "error": str(e)}

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
