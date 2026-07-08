"""
POD Fulfillment Service — step 81-2f.

Orchestrates the full print-on-demand loop:
  create_product_for_task()  — image upload + blueprint selection + Printify product
  submit_order()             — Etsy receipt → Printify order + FulfillmentRecord
  sync_tracking()            — Printify shipment → Etsy tracking push

Called automatically by EtsyReceiptWorker; no manual trigger.

Etsy ShopReceipt address field names (from Open API v3 spec):
  name        — buyer's full name
  first_line  — address line 1
  second_line — address line 2 (nullable)
  city, state, zip, country_iso

Etsy createReceiptShipment endpoint:
  POST /v3/application/shops/{shop_id}/receipts/{receipt_id}/tracking
  Body: tracking_code, carrier_name
"""
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError

from app.agents.product_type_selector_agent import ProductTypeSelectorAgent
from app.db.database import SessionLocal
from app.models.fulfillment_record import FulfillmentRecord
from app.models.pod_product import PODProduct
from app.services.image_catalog_service import ImageCatalogService
from app.services.printify_client import PrintifyClient
from app.services.etsy_oauth import get_valid_access_token
from config import settings

logger = logging.getLogger("ai-factory")

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


class PODFulfillmentService:
    def __init__(
        self,
        printify_client: Optional[PrintifyClient] = None,
        selector_agent: Optional[ProductTypeSelectorAgent] = None,
    ):
        self._printify = printify_client or PrintifyClient()
        self._selector = selector_agent or ProductTypeSelectorAgent()
        self._catalog = ImageCatalogService()

    # ── Product creation ─────────────────────────────────────────────────────

    def create_product_for_task(
        self, task_id: str, etsy_listing_id: Optional[str] = None
    ) -> PODProduct:
        """
        Orchestrate end-to-end POD product creation for a task:
          1. Locate delivery asset (POD design) from ImageCatalog
          2. Upload image to Printify
          3. Use ProductTypeSelectorAgent to pick best blueprint
          4. Auto-select first available print provider + enabled variants
          5. Create Printify product
          6. Persist PODProduct row

        Returns the saved PODProduct instance.
        """
        # 1. Find the delivery-variant design file
        asset = self._catalog.get_delivery_asset(task_id)
        if not asset:
            raise RuntimeError(
                f"No delivery asset found for task {task_id}. "
                "Run PODDesignAgent first (step 71)."
            )
        image_path = asset.local_path
        if not Path(image_path).exists():
            raise FileNotFoundError(f"Delivery asset file missing on disk: {image_path}")

        # 2. Upload design to Printify
        logger.info(f"PODFulfillmentService: uploading image for task {task_id}")
        printify_image_id = self._printify.upload_image(image_path)

        # 3. Fetch blueprint catalog and pick best fit
        blueprints_raw = self._printify.list_blueprints()
        blueprints = [{"id": bp["id"], "title": bp.get("title", "")} for bp in blueprints_raw[:80]]

        concept = f"task_id={task_id}"  # Caller can pass richer context via a task lookup
        selection = self._selector.select(concept, blueprints)
        blueprint_id = int(selection["blueprint_id"])

        # 4. Pick print provider (first available)
        providers = self._printify.list_print_providers(blueprint_id)
        if not providers:
            raise RuntimeError(f"No print providers for blueprint {blueprint_id}")
        print_provider_id = int(providers[0]["id"])

        # 5. Pick variants (all enabled ones, up to 10)
        variants_resp = self._printify.list_variants(blueprint_id, print_provider_id)
        all_variants = variants_resp.get("variants", [])
        enabled_variant_ids = [v["id"] for v in all_variants if v.get("is_enabled", True)][:10]
        if not enabled_variant_ids:
            enabled_variant_ids = [all_variants[0]["id"]] if all_variants else []

        # 6. Create Printify product
        title = f"AI Factory Product — task {task_id[:8]}"
        printify_product = self._printify.create_product(
            blueprint_id=blueprint_id,
            print_provider_id=print_provider_id,
            variant_ids=enabled_variant_ids,
            image_id=printify_image_id,
            title=title,
        )
        printify_product_id = str(printify_product["id"])

        # 7. Persist PODProduct
        db = SessionLocal()
        try:
            pod = PODProduct(
                id=str(uuid.uuid4()),
                task_id=task_id,
                printify_product_id=printify_product_id,
                blueprint_id=blueprint_id,
                print_provider_id=print_provider_id,
                variant_ids=enabled_variant_ids,
                etsy_listing_id=etsy_listing_id,
                created_at=datetime.utcnow(),
            )
            db.add(pod)
            db.commit()
            db.refresh(pod)
            logger.info(
                f"PODFulfillmentService: created PODProduct {pod.id} "
                f"(printify={printify_product_id}, blueprint={blueprint_id})"
            )
            return pod
        finally:
            db.close()

    def set_etsy_listing_id(self, pod_product_id: str, etsy_listing_id: str) -> bool:
        """
        Link a PODProduct row to the Etsy listing created for it. Called by
        PipelineOrchestrator after create_product_for_task() ran as a
        pre-listing precondition check (no listing existed yet at that point).

        Returns True if a record was found and updated, False otherwise.
        """
        db = SessionLocal()
        try:
            pod = db.query(PODProduct).filter(PODProduct.id == pod_product_id).first()
            if not pod:
                return False
            pod.etsy_listing_id = etsy_listing_id
            db.commit()
            return True
        finally:
            db.close()

    # ── Order submission ─────────────────────────────────────────────────────

    def submit_order(
        self,
        receipt_id: str,
        task_id: str,
        pod_product_id: str,
        shipping_address: dict,
        variant_id: int,
        quantity: int = 1,
        transaction_id: str = "",
    ) -> FulfillmentRecord:
        """
        Create a Printify order and record it as a FulfillmentRecord.

        Called automatically by EtsyReceiptWorker. Not intended for manual use.
        transaction_id is the Etsy ShopTransaction.transaction_id — together with
        receipt_id it forms the composite unique key that makes multi-item receipts
        work correctly (one FulfillmentRecord per transaction, not per receipt).
        """
        # Look up Printify product_id
        db = SessionLocal()
        try:
            pod = db.query(PODProduct).filter(PODProduct.id == pod_product_id).first()
            if not pod:
                raise RuntimeError(f"PODProduct {pod_product_id} not found")
            printify_product_id = pod.printify_product_id
        finally:
            db.close()

        logger.info(
            f"PODFulfillmentService: submitting Printify order for receipt {receipt_id}"
        )
        printify_order_id = self._printify.create_order(
            product_id=printify_product_id,
            variant_id=variant_id,
            quantity=quantity,
            shipping_address=shipping_address,
        )

        db = SessionLocal()
        try:
            record = FulfillmentRecord(
                id=str(uuid.uuid4()),
                etsy_receipt_id=str(receipt_id),
                etsy_transaction_id=str(transaction_id),
                task_id=task_id,
                pod_product_id=pod_product_id,
                printify_order_id=printify_order_id,
                status="submitted",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            try:
                db.add(record)
                db.commit()
                db.refresh(record)
            except IntegrityError:
                # Another concurrent thread already inserted the same
                # (receipt_id, transaction_id). Roll back cleanly and return
                # the winning record. This is expected under concurrent load —
                # do NOT alert; it's not an actionable failure.
                db.rollback()
                logger.info(
                    f"PODFulfillmentService: concurrent duplicate for receipt "
                    f"{receipt_id} txn {transaction_id} — already handled by "
                    f"another thread, returning existing record"
                )
                record = (
                    db.query(FulfillmentRecord)
                    .filter(
                        FulfillmentRecord.etsy_receipt_id == str(receipt_id),
                        FulfillmentRecord.etsy_transaction_id == str(transaction_id),
                    )
                    .first()
                )
            logger.info(
                f"PODFulfillmentService: FulfillmentRecord {record.id} "
                f"(printify_order={printify_order_id})"
            )
            return record
        finally:
            db.close()

    # ── Tracking sync ─────────────────────────────────────────────────────────

    def sync_tracking(self, fulfillment_record_id: str) -> bool:
        """
        Check Printify order status and push tracking to Etsy if shipped.

        Returns True if tracking was synced this call, False if order is
        still in progress or was already synced.
        """
        db = SessionLocal()
        try:
            record = (
                db.query(FulfillmentRecord)
                .filter(FulfillmentRecord.id == fulfillment_record_id)
                .first()
            )
            if not record:
                return False
            if record.status == "tracking_synced":
                return False
            if not record.printify_order_id:
                return False

            receipt_id = record.etsy_receipt_id
            printify_order_id = record.printify_order_id
        finally:
            db.close()

        # Check Printify for shipment info
        order = self._printify.get_order_status(printify_order_id)
        shipments = order.get("shipments", [])
        if not shipments:
            return False

        shipment = shipments[0]
        tracking_number = shipment.get("number", "")
        carrier = shipment.get("carrier", "")
        if not tracking_number:
            return False

        # Push tracking to Etsy
        import asyncio
        asyncio.run(self._push_tracking_to_etsy(receipt_id, tracking_number, carrier))

        # Update DB
        db = SessionLocal()
        try:
            record = (
                db.query(FulfillmentRecord)
                .filter(FulfillmentRecord.id == fulfillment_record_id)
                .first()
            )
            if record:
                record.tracking_number = tracking_number
                record.carrier = carrier
                record.status = "tracking_synced"
                record.updated_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()

        logger.info(
            f"PODFulfillmentService: tracking synced for receipt {receipt_id} "
            f"({carrier} {tracking_number})"
        )
        return True

    async def _push_tracking_to_etsy(
        self, receipt_id: str, tracking_number: str, carrier: str
    ):
        """
        POST /v3/application/shops/{shop_id}/receipts/{receipt_id}/tracking
        Required scope: transactions_w
        """
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/receipts/{receipt_id}/tracking",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "tracking_code": tracking_number,
                    "carrier_name": carrier,
                },
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Etsy tracking push failed {resp.status_code}: {resp.text[:200]}"
                )
