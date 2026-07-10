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
import math
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

# P0-12: keep the physical product honest to the always-T-shirt taxonomy (482).
# Rather than hardcode brittle blueprint IDs, curate the LIVE catalog down to
# t-shirt blueprints by title, so the selector always picks a real, in-catalog
# tee that matches the listing category — never an arbitrary mug/poster from the
# first 80 blueprints described only by "task_id=...".
_TEE_TITLE_MARKERS = ("t-shirt", "t shirt", "tee", "jersey")
_PREFERRED_VARIANT = [("l", "black"), ("l", "white"), ("m", "black"), ("m", "white")]


def _title_has_size(title: str, size: str) -> bool:
    # Variant titles look like "Black / L" or "L / Black" — match the size as a
    # standalone slash/space-delimited token, not a substring (avoid 'l' in 'black').
    tokens = [t.strip().lower() for part in title.split("/") for t in part.split()]
    return size in tokens


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
        self, task_id: str, etsy_listing_id: Optional[str] = None, concept: Optional[str] = None
    ) -> PODProduct:
        """
        Orchestrate end-to-end POD product creation for a task:
          1. Locate delivery asset (POD design) from ImageCatalog
          2. Upload image to Printify
          3. Pick a t-shirt blueprint (curated from the live catalog) using the
             REAL product concept (P0-12), not a meaningless task_id string
          4. Pick first available print provider + ONE deliberate variant (P0-5)
          5. Create Printify product with a margin-safe price (P0-4)
          6. Persist PODProduct row (with cost/price/variant for auditing)

        `concept` should be the real product name + brief; if omitted it's looked
        up from the task so the blueprint selector has something meaningful.
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

        # 3. Curate the catalog to t-shirt blueprints and select with the real concept
        concept = concept or self._lookup_concept(task_id)
        blueprints_raw = self._printify.list_blueprints()
        tee_blueprints = [
            bp for bp in blueprints_raw
            if any(m in (bp.get("title", "").lower()) for m in _TEE_TITLE_MARKERS)
        ]
        if not tee_blueprints:
            logger.warning("PODFulfillmentService: no tee blueprints found by title; falling back to first 80")
            tee_blueprints = blueprints_raw[:80]
        blueprints = [{"id": bp["id"], "title": bp.get("title", "")} for bp in tee_blueprints[:60]]

        selection = self._selector.select(concept, blueprints)
        blueprint_id = int(selection["blueprint_id"])
        # Guard: the selector must return an id that is actually in the curated list.
        valid_ids = {b["id"] for b in blueprints}
        if blueprint_id not in valid_ids:
            blueprint_id = blueprints[0]["id"]
        logger.info(f"PODFulfillmentService: task {task_id} concept={concept[:60]!r} -> blueprint {blueprint_id}")

        # 4. Pick print provider (first available)
        providers = self._printify.list_print_providers(blueprint_id)
        if not providers:
            raise RuntimeError(f"No print providers for blueprint {blueprint_id}")
        print_provider_id = int(providers[0]["id"])

        # 5. Pick ONE deliberate variant (P0-5) — a mid-size neutral tee — instead
        # of 10 arbitrary ones the buyer can't even choose between.
        variants_resp = self._printify.list_variants(blueprint_id, print_provider_id)
        all_variants = variants_resp.get("variants", [])
        chosen_variant = self._pick_single_variant(all_variants)
        if not chosen_variant:
            raise RuntimeError(f"No variants available for blueprint {blueprint_id}")
        variant_id = chosen_variant["id"]
        variant_title = chosen_variant.get("title", "")
        enabled_variant_ids = [variant_id]

        # 6. Create Printify product with the real title (P1-8). Price is set from
        # cost after readback (P0-4); provisional high price avoids a $0 product
        # if the readback can't produce a cost.
        title = concept[:120] or f"AI Factory Product — task {task_id[:8]}"
        provisional_price_cents = int((getattr(settings, "POD_TARGET_PROFIT_USD", 6.0) + 30) * 100)
        printify_product = self._printify.create_product(
            blueprint_id=blueprint_id,
            print_provider_id=print_provider_id,
            variant_ids=enabled_variant_ids,
            image_id=printify_image_id,
            title=title,
            price_cents=provisional_price_cents,
        )
        printify_product_id = str(printify_product["id"])

        # 6b. Readback: confirm the image is attached AND read the real variant cost.
        product = self._verify_product_readback(printify_product_id, printify_image_id)
        cost_cents = self._variant_cost_cents(product, variant_id)
        price_cents = self._pod_price_cents_from_cost(cost_cents) if cost_cents else None
        if cost_cents and price_cents:
            logger.info(
                f"PODFulfillmentService: variant cost {cost_cents}c -> margin-safe "
                f"Etsy price {price_cents}c (task {task_id})"
            )
        else:
            logger.warning(
                f"PODFulfillmentService: no variant cost from readback for task {task_id}; "
                "Etsy price will fall back to the format band (still >= cost floor via band)"
            )

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
                cost_cents=cost_cents,
                price_cents=price_cents,
                variant_title=variant_title,
                created_at=datetime.utcnow(),
            )
            db.add(pod)
            db.commit()
            db.refresh(pod)
            logger.info(
                f"PODFulfillmentService: created PODProduct {pod.id} "
                f"(printify={printify_product_id}, blueprint={blueprint_id}, variant={variant_title!r})"
            )
            return pod
        finally:
            db.close()

    def _lookup_concept(self, task_id: str) -> str:
        """Build a real concept string (title + description) from the task's
        output_data so the blueprint selector isn't fed a meaningless id (P0-12)."""
        try:
            from app.services.task_service import TaskService
            task = TaskService().get_task(task_id)
            output = getattr(task, "output_data", None) or {}
            name = output.get("title") or output.get("product_name") or ""
            desc = output.get("description") or ""
            concept = f"{name}. {desc}".strip()
            return concept or f"task_id={task_id}"
        except Exception:
            return f"task_id={task_id}"

    def _pick_single_variant(self, all_variants: list) -> Optional[dict]:
        enabled = [v for v in all_variants if v.get("is_enabled", True)] or list(all_variants)
        if not enabled:
            return None
        for size, color in _PREFERRED_VARIANT:
            for v in enabled:
                t = (v.get("title") or "").lower()
                if _title_has_size(v.get("title", ""), size) and color in t:
                    return v
        # No neutral L/M found — take a middle enabled variant (avoids always-XS).
        return enabled[len(enabled) // 2]

    @staticmethod
    def _variant_cost_cents(product: dict, variant_id: int) -> Optional[int]:
        """Read the sold variant's production cost (cents) from the product readback."""
        for v in product.get("variants", []) or []:
            if v.get("id") == variant_id and v.get("cost"):
                return int(v["cost"])
        # Fallback: max cost across variants if the specific one isn't found.
        costs = [int(v["cost"]) for v in product.get("variants", []) or [] if v.get("cost")]
        return max(costs) if costs else None

    @staticmethod
    def _pod_price_cents_from_cost(cost_cents: int) -> int:
        """P0-4 margin math: price = ceil((cost + shipping + $0.20 + profit) /
        (1 - fee_fraction)), rounded UP to a whole dollar to protect margin."""
        cost = cost_cents / 100.0
        shipping = getattr(settings, "POD_SHIPPING_ESTIMATE_USD", 5.0)
        profit = getattr(settings, "POD_TARGET_PROFIT_USD", 6.0)
        fee = getattr(settings, "POD_ETSY_FEE_FRACTION", 0.10)
        raw = (cost + shipping + 0.20 + profit) / (1 - fee)
        return int(math.ceil(raw) * 100)

    def _verify_product_readback(self, printify_product_id: str, expected_image_id: str) -> dict:
        """
        Re-fetch the just-created product from Printify (not the create
        response) and confirm the submitted image_id is actually present in
        at least one print area placeholder. Raises RuntimeError if not —
        the caller treats this the same as any other creation failure.
        Returns the product dict so the caller can also read variant costs.
        """
        product = self._printify.get_product(printify_product_id)
        attached_image_ids = {
            img.get("id")
            for area in product.get("print_areas", [])
            for placeholder in area.get("placeholders", [])
            for img in placeholder.get("images", [])
        }
        if expected_image_id not in attached_image_ids:
            raise RuntimeError(
                f"Printify readback failed: product {printify_product_id} does not have "
                f"image {expected_image_id} attached (found: {attached_image_ids or 'none'})"
            )
        return product

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

        # P2-5: a multi-parcel order has several shipments — push EVERY tracking
        # number to Etsy (Etsy accepts multiple tracking posts per receipt),
        # not just the first, so no parcel goes untracked.
        tracked = [s for s in shipments if s.get("number")]
        if not tracked:
            return False

        import asyncio
        for s in tracked:
            asyncio.run(self._push_tracking_to_etsy(receipt_id, s.get("number", ""), s.get("carrier", "")))

        # Record the last tracking number/carrier on the record for reference.
        tracking_number = tracked[-1].get("number", "")
        carrier = tracked[-1].get("carrier", "")

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
