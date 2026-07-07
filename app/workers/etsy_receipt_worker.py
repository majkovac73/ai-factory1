"""
Etsy Receipt Worker — steps 81-2g and 81-2h.

Background thread that:
  1. Polls Etsy's getShopReceipts every ETSY_RECEIPT_POLL_SECONDS seconds
     for new paid receipts, auto-submitting Printify orders for any that
     match a known PODProduct.
  2. Also syncs tracking for FulfillmentRecords still in "submitted" status,
     pushing tracking back to Etsy automatically once Printify ships.

State (last_checked_at) persists across restarts in:
  data/receipt_worker_state.json

Etsy ShopReceipt field names used (from Open API v3 spec):
  receipt_id          — integer ID of the receipt
  name                — buyer's full name (for splitting into first/last)
  first_line          — shipping address line 1
  second_line         — shipping address line 2 (nullable)
  city, state, zip, country_iso
  transactions[]      — array of ShopTransaction objects
    transaction.listing_id  — Etsy listing ID (joins to PODProduct)
    transaction.quantity    — quantity purchased
"""
import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.db.database import SessionLocal
from app.models.fulfillment_record import FulfillmentRecord
from app.models.pod_product import PODProduct
from app.services.etsy_oauth import get_valid_access_token
from app.services.pod_fulfillment_service import PODFulfillmentService
from config import settings

logger = logging.getLogger("ai-factory")

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
STATE_FILE = Path(__file__).resolve().parents[2] / "data" / "receipt_worker_state.json"


class EtsyReceiptWorker:
    """
    Mirror of TaskWorker's structure: background daemon thread,
    start()/stop(), poll loop with a stop event.
    """

    def __init__(
        self,
        poll_seconds: Optional[int] = None,
        fulfillment_service: Optional[PODFulfillmentService] = None,
    ):
        self._poll_seconds = poll_seconds or getattr(settings, "ETSY_RECEIPT_POLL_SECONDS", 300)
        self._fulfillment = fulfillment_service or PODFulfillmentService()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("EtsyReceiptWorker: start() called but worker already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="EtsyReceiptWorker")
        self._thread.start()
        logger.info(f"EtsyReceiptWorker: started (poll every {self._poll_seconds}s)")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("EtsyReceiptWorker: stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._poll_new_receipts()
            except Exception as e:
                logger.error(f"EtsyReceiptWorker: error polling receipts: {e}")

            try:
                self._sync_pending_tracking()
            except Exception as e:
                logger.error(f"EtsyReceiptWorker: error syncing tracking: {e}")

            self._stop_event.wait(self._poll_seconds)

    # ── New-receipt polling ───────────────────────────────────────────────────

    def _poll_new_receipts(self):
        state = self._load_state()
        last_checked = state.get("last_checked_at", 0)

        try:
            receipts = asyncio.run(self._fetch_receipts(min_created=last_checked))
        except Exception as e:
            logger.error(f"EtsyReceiptWorker: failed to fetch receipts: {e}")
            return

        now_ts = int(datetime.now(timezone.utc).timestamp())
        processed = 0
        for receipt in receipts:
            try:
                self._process_receipt(receipt)
                processed += 1
            except Exception as e:
                receipt_id = receipt.get("receipt_id", "?")
                logger.error(f"EtsyReceiptWorker: error processing receipt {receipt_id}: {e}")

        if processed or receipts:
            logger.info(
                f"EtsyReceiptWorker: poll done — {len(receipts)} receipts fetched, "
                f"{processed} processed"
            )

        state["last_checked_at"] = now_ts
        self._save_state(state)

    async def _fetch_receipts(self, min_created: int = 0):
        if not settings.ETSY_SHOP_ID:
            logger.warning("EtsyReceiptWorker: ETSY_SHOP_ID not set, skipping poll")
            return []

        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

        params = {"was_paid": "true", "limit": 100}
        if min_created > 0:
            params["min_created"] = str(min_created)

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/receipts",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": api_key_header,
                },
                params=params,
            )

        if resp.status_code == 401:
            logger.error("EtsyReceiptWorker: 401 Unauthorized — re-authorize at /etsy/oauth/login")
            return []
        if resp.status_code >= 400:
            logger.error(f"EtsyReceiptWorker: Etsy API {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        return data.get("results", [])

    def _process_receipt(self, receipt: dict):
        receipt_id = str(receipt.get("receipt_id", ""))
        if not receipt_id:
            return

        transactions = receipt.get("transactions", [])
        if not transactions:
            return

        # Build shipping address from Etsy ShopReceipt fields
        full_name = receipt.get("name", "")
        name_parts = full_name.split(" ", 1) if full_name else ["", ""]
        shipping_address = {
            "first_name": name_parts[0],
            "last_name": name_parts[1] if len(name_parts) > 1 else "",
            "address1": receipt.get("first_line", ""),
            "address2": receipt.get("second_line") or "",
            "city": receipt.get("city", ""),
            "region": receipt.get("state", ""),
            "country": receipt.get("country_iso", "US"),
            "zip": receipt.get("zip", ""),
            "email": "",
            "phone": "",
        }

        for transaction in transactions:
            listing_id = str(transaction.get("listing_id", ""))
            quantity = int(transaction.get("quantity", 1))

            if not listing_id:
                continue

            # Find matching PODProduct by Etsy listing
            db = SessionLocal()
            try:
                pod = (
                    db.query(PODProduct)
                    .filter(PODProduct.etsy_listing_id == listing_id)
                    .first()
                )
                if pod is None:
                    # Digital download or unlisted POD — no action needed
                    continue

                # Idempotency: skip if already processed this receipt
                existing = (
                    db.query(FulfillmentRecord)
                    .filter(FulfillmentRecord.etsy_receipt_id == receipt_id)
                    .first()
                )
                if existing:
                    continue

                pod_id = pod.id
                task_id = pod.task_id
                variant_ids = pod.variant_ids or []
                variant_id = variant_ids[0] if variant_ids else 0
            finally:
                db.close()

            self._fulfillment.submit_order(
                receipt_id=receipt_id,
                task_id=task_id,
                pod_product_id=pod_id,
                shipping_address=shipping_address,
                variant_id=variant_id,
                quantity=quantity,
            )
            # One Printify order per receipt (unique constraint on etsy_receipt_id)
            break

    # ── Tracking sync ─────────────────────────────────────────────────────────

    def _sync_pending_tracking(self):
        db = SessionLocal()
        try:
            pending = (
                db.query(FulfillmentRecord)
                .filter(FulfillmentRecord.status == "submitted")
                .all()
            )
            record_ids = [r.id for r in pending]
        finally:
            db.close()

        for record_id in record_ids:
            try:
                synced = self._fulfillment.sync_tracking(record_id)
                if synced:
                    logger.info(f"EtsyReceiptWorker: tracking synced for FulfillmentRecord {record_id}")
            except Exception as e:
                logger.error(f"EtsyReceiptWorker: error syncing tracking for {record_id}: {e}")

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_state(self, state: dict):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
