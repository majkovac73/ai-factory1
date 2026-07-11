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
from app.services import worker_registry
from config import settings

logger = logging.getLogger("ai-factory")

from app.core.paths import get_data_dir

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"
STATE_FILE = get_data_dir() / "receipt_worker_state.json"


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
        try:
            _check_interval = 0
            while not self._stop_event.is_set():
                worker_registry.record_heartbeat("EtsyReceiptWorker")
                try:
                    self._poll_new_receipts()
                except Exception as e:
                    logger.error(f"EtsyReceiptWorker: error polling receipts: {e}")

                try:
                    self._sync_pending_tracking()
                except Exception as e:
                    logger.error(f"EtsyReceiptWorker: error syncing tracking: {e}")

                # Internal self-check every 3 poll cycles (~15 min at default 300s).
                # Weaker than an external check (can't detect whole-process death),
                # but provides in-process visibility without a second service.
                _check_interval += 1
                if _check_interval >= 3:
                    _check_interval = 0
                    self._check_worker_health()

                # C-3: daily backup tick (folded into this worker's loop).
                try:
                    self._maybe_backup()
                except Exception as e:
                    logger.error(f"EtsyReceiptWorker: backup tick failed: {e}")

                # A-10: daily views/favorites poll (earliest sales signal).
                try:
                    self._maybe_poll_listing_stats()
                except Exception as e:
                    logger.error(f"EtsyReceiptWorker: listing-stats tick failed: {e}")

                # Disk hygiene: prune old generated images so the volume doesn't fill.
                try:
                    self._maybe_cleanup_images()
                except Exception as e:
                    logger.error(f"EtsyReceiptWorker: image-cleanup tick failed: {e}")

                self._stop_event.wait(self._poll_seconds)
        finally:
            if not self._stop_event.is_set():
                logger.critical("EtsyReceiptWorker: thread exiting unexpectedly")
                try:
                    from app.services.alert_service import AlertService
                    AlertService().send_alert_sync(
                        "EtsyReceiptWorker thread died",
                        "EtsyReceiptWorker exited its run loop without being stopped. "
                        "New Etsy orders will not be fulfilled until the service restarts.",
                        level="error",
                    )
                except Exception:
                    pass

    # ── New-receipt polling ───────────────────────────────────────────────────

    @staticmethod
    def _receipt_last_modified(receipt: dict) -> int:
        """Etsy exposes the receipt's last-modified time under a couple of field
        spellings across API eras; fall back to create/now so we always have one."""
        for key in ("updated_timestamp", "update_timestamp", "last_modified_timestamp"):
            v = receipt.get(key)
            if v:
                return int(v)
        for key in ("created_timestamp", "create_timestamp"):
            v = receipt.get(key)
            if v:
                return int(v)
        return int(datetime.now(timezone.utc).timestamp())

    def _poll_new_receipts(self):
        state = self._load_state()
        # min_last_modified (P0-7): unlike min_created, this catches receipts
        # created before the checkpoint but PAID/updated after it.
        last_checked = state.get("last_checked_at", 0)
        failed = dict(state.get("failed_receipts", {}))  # receipt_id -> {attempts, last_modified, first_seen}

        try:
            receipts = asyncio.run(self._fetch_receipts(min_last_modified=last_checked))
        except Exception as e:
            logger.error(f"EtsyReceiptWorker: failed to fetch receipts: {e}")
            return

        now_ts = int(datetime.now(timezone.utc).timestamp())
        processed = 0
        for receipt in receipts:
            receipt_id = str(receipt.get("receipt_id", "?"))
            try:
                ok = self._process_receipt(receipt)
                processed += 1
            except Exception as e:
                ok = False
                logger.error(f"EtsyReceiptWorker: error processing receipt {receipt_id}: {e}")

            if ok:
                failed.pop(receipt_id, None)
            else:
                entry = failed.get(receipt_id) or {"attempts": 0, "first_seen": now_ts}
                entry["attempts"] = entry.get("attempts", 0) + 1
                entry["last_modified"] = self._receipt_last_modified(receipt)
                failed[receipt_id] = entry

        # Give up (loudly) on receipts that have exhausted their retries so the
        # checkpoint isn't held back forever by one permanently-broken order.
        max_attempts = getattr(settings, "FULFILLMENT_MAX_RETRY_ATTEMPTS", 5)
        for rid, entry in list(failed.items()):
            if entry.get("attempts", 0) >= max_attempts:
                self._alert_giving_up(rid, entry.get("attempts", 0))
                failed.pop(rid, None)

        # Checkpoint: advance to now, but never PAST a still-retriable failed
        # receipt — hold it back to just before that receipt's last-modified so
        # the next poll re-fetches (and retries) it. Idempotency makes the
        # re-processing of already-succeeded receipts safe.
        new_checkpoint = now_ts
        if failed:
            oldest_failed = min(e.get("last_modified", now_ts) for e in failed.values())
            new_checkpoint = min(new_checkpoint, max(oldest_failed - 1, 0))

        if processed or receipts or failed:
            logger.info(
                f"EtsyReceiptWorker: poll done — {len(receipts)} receipts fetched, "
                f"{processed} processed, {len(failed)} awaiting retry"
            )

        state["last_checked_at"] = new_checkpoint
        state["failed_receipts"] = failed
        self._save_state(state)

    async def _fetch_receipts(self, min_last_modified: int = 0):
        if not settings.ETSY_SHOP_ID:
            logger.warning("EtsyReceiptWorker: ETSY_SHOP_ID not set, skipping poll")
            return []

        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "x-api-key": api_key_header,
        }

        # P2-7: page through results — a viral day or a first poll after long
        # downtime can produce >100 receipts; without paging the tail is dropped
        # (= unfulfilled orders).
        results = []
        offset = 0
        limit = 100
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                params = {"was_paid": "true", "limit": limit, "offset": offset}
                if min_last_modified > 0:
                    params["min_last_modified"] = str(min_last_modified)

                resp = await client.get(
                    f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/receipts",
                    headers=headers,
                    params=params,
                )
                if resp.status_code == 401:
                    logger.error("EtsyReceiptWorker: 401 Unauthorized — re-authorize at /etsy/oauth/login")
                    return results
                if resp.status_code >= 400:
                    logger.error(f"EtsyReceiptWorker: Etsy API {resp.status_code}: {resp.text[:200]}")
                    return results

                page = resp.json().get("results", [])
                results.extend(page)
                if len(page) < limit:
                    break
                offset += limit
        return results

    async def _fetch_receipt_by_id(self, receipt_id: str) -> Optional[dict]:
        """Fetch a single receipt (used by the manual resubmit endpoint, P0-7)."""
        access_token = await get_valid_access_token()
        api_key_header = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/receipts/{receipt_id}",
                headers={"Authorization": f"Bearer {access_token}", "x-api-key": api_key_header},
            )
        if resp.status_code >= 400:
            raise Exception(f"Etsy API {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def process_receipt_by_id(self, receipt_id: str) -> dict:
        """Manual recovery entrypoint (POST /pod/fulfillments/resubmit/{id}).
        Fetches the receipt and re-runs processing; idempotent for anything
        already fulfilled/recorded. Also clears it from the failed set."""
        receipt = asyncio.run(self._fetch_receipt_by_id(str(receipt_id)))
        ok = self._process_receipt(receipt)
        state = self._load_state()
        failed = dict(state.get("failed_receipts", {}))
        if ok:
            failed.pop(str(receipt_id), None)
            state["failed_receipts"] = failed
            self._save_state(state)
        return {"receipt_id": str(receipt_id), "ok": ok}

    def _process_receipt(self, receipt: dict) -> bool:
        """Process one receipt. Returns True only if every POD transaction was
        fulfilled successfully (digital-only receipts are trivially True), so
        the poller knows whether to retry. Revenue recording (P0-8) is
        best-effort and does not affect the return value."""
        receipt_id = str(receipt.get("receipt_id", ""))
        if not receipt_id:
            return True

        transactions = receipt.get("transactions", [])
        if not transactions:
            return True

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
            # P2-6: forward buyer contact info — some providers/carriers need it.
            "email": receipt.get("buyer_email") or "",
            "phone": receipt.get("buyer_phone") or receipt.get("phone") or "",
        }

        all_ok = True
        # Each transaction is processed independently — a receipt with two
        # different POD listings must generate two separate Printify orders.
        # Idempotency is per (receipt_id, transaction_id), not per receipt.
        for transaction in transactions:
            listing_id = str(transaction.get("listing_id", ""))
            transaction_id = str(transaction.get("transaction_id", ""))
            quantity = int(transaction.get("quantity", 1))

            if not listing_id:
                continue

            db = SessionLocal()
            try:
                pod = (
                    db.query(PODProduct)
                    .filter(PODProduct.etsy_listing_id == listing_id)
                    .first()
                )
                pod_id = pod.id if pod else None
                pod_task_id = pod.task_id if pod else None
                variant_ids = (pod.variant_ids or []) if pod else []
                variant_id = variant_ids[0] if variant_ids else 0

                # Idempotency for fulfillment: per (receipt_id, transaction_id)
                already_fulfilled = False
                if pod is not None:
                    already_fulfilled = (
                        db.query(FulfillmentRecord)
                        .filter(
                            FulfillmentRecord.etsy_receipt_id == receipt_id,
                            FulfillmentRecord.etsy_transaction_id == transaction_id,
                        )
                        .first()
                        is not None
                    )
            finally:
                db.close()

            # P0-8: record revenue for EVERY transaction (POD + digital), tied
            # back to the generating task, idempotent on transaction_id.
            task_id = pod_task_id or self._resolve_digital_task_id(listing_id)
            self._record_revenue(task_id, transaction, quantity, transaction_id)

            # POD fulfillment (digital downloads need none — Etsy delivers them).
            if pod is None:
                continue
            if already_fulfilled:
                continue

            try:
                self._fulfillment.submit_order(
                    receipt_id=receipt_id,
                    task_id=pod_task_id,
                    pod_product_id=pod_id,
                    shipping_address=shipping_address,
                    variant_id=variant_id,
                    quantity=quantity,
                    transaction_id=transaction_id,
                )
            except Exception as fulfillment_err:
                all_ok = False
                logger.error(
                    f"EtsyReceiptWorker: fulfillment failed for "
                    f"receipt={receipt_id} transaction={transaction_id}: {fulfillment_err}"
                )
                try:
                    from app.services.alert_service import AlertService
                    AlertService().send_alert_sync(
                        "Fulfillment order failed",
                        f"Could not submit Printify order for Etsy receipt {receipt_id} "
                        f"transaction {transaction_id}.\nError: {fulfillment_err}\n"
                        "It will be retried automatically on the next poll "
                        f"(up to {getattr(settings, 'FULFILLMENT_MAX_RETRY_ATTEMPTS', 5)} attempts). "
                        "Manual resubmit: POST /pod/fulfillments/resubmit/{receipt_id}.",
                        level="error",
                    )
                except Exception:
                    pass

        return all_ok

    @staticmethod
    def _resolve_digital_task_id(listing_id: str) -> Optional[str]:
        """Map an Etsy listing_id back to the task that generated it, for digital
        (non-POD) sales — the image catalog persisted listing_id on publish."""
        from app.models.image_asset import ImageAsset
        db = SessionLocal()
        try:
            asset = (
                db.query(ImageAsset)
                .filter(ImageAsset.listing_id == str(listing_id))
                .first()
            )
            return asset.task_id if asset else None
        finally:
            db.close()

    def _record_revenue(self, task_id, transaction: dict, quantity: int, transaction_id: str):
        """Best-effort, idempotent revenue recording for one transaction (P0-8)."""
        if not task_id:
            logger.info(
                f"EtsyReceiptWorker: no task resolved for transaction {transaction_id} "
                f"(listing {transaction.get('listing_id')}); revenue not recorded"
            )
            return
        price = transaction.get("price") or {}
        amount = price.get("amount")
        divisor = price.get("divisor") or 100
        if amount is None:
            return
        unit_price = amount / divisor
        line_total = unit_price * max(quantity, 1)
        if line_total <= 0:
            return
        currency = price.get("currency_code", "USD")
        try:
            from app.services.revenue_service import RevenueService
            rev = RevenueService()
            if rev.has_sale_for_transaction(transaction_id):
                return
            rev.record_sale(
                task_id=task_id,
                amount=line_total,
                currency=currency,
                quantity=max(quantity, 1),
                notes=f"Etsy transaction {transaction_id}",
                transaction_id=transaction_id,
            )
            logger.info(
                f"EtsyReceiptWorker: recorded sale {currency} {line_total:.2f} "
                f"for task {task_id} (transaction {transaction_id})"
            )
            # A-1: a real sale is the strongest signal there is — spawn a
            # variant of the winner (capped, respects the kill switch).
            self._maybe_spawn_winner_variant(task_id)
        except Exception as e:
            logger.error(f"EtsyReceiptWorker: failed to record revenue for transaction {transaction_id}: {e}")

    def _alert_giving_up(self, receipt_id: str, attempts: int):
        logger.critical(
            f"EtsyReceiptWorker: giving up on receipt {receipt_id} after {attempts} attempts"
        )
        try:
            from app.services.alert_service import AlertService
            AlertService().send_alert_sync(
                "Fulfillment PERMANENTLY failed — manual action needed",
                f"Etsy receipt {receipt_id} could not be fulfilled after {attempts} "
                "attempts and will no longer be retried automatically. A customer has "
                "PAID and will not receive their order unless you act. Fix the cause and "
                "resubmit: POST /pod/fulfillments/resubmit/" + str(receipt_id),
                level="error",
            )
        except Exception:
            pass

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

    # ── Internal worker health self-check ────────────────────────────────────

    def _check_worker_health(self):
        """
        Check heartbeat registry, RESTART any worker whose thread has died
        (P3-5), and alert via Discord about stale workers. Limitation: if the
        whole process dies, this check dies with it; Railway's crash/restart
        notifications act as the external backstop.
        """
        stale_thresholds = {
            "TaskWorker": 30,                 # should beat every ~1s
            "EtsyReceiptWorker": 660,         # beats every poll cycle (300s), allow 2x
            "AutonomyWorker": 7200,           # beats every schedule cycle (3600s), allow 2x
            "MarketingRefreshWorker": 43200,  # beats every schedule cycle (21600s), allow 2x
        }
        stale = [
            name
            for name, max_age in stale_thresholds.items()
            if worker_registry.is_stale(name, max_age)
        ]
        if not stale:
            return

        logger.warning(f"EtsyReceiptWorker: stale heartbeats detected: {stale}")

        # P3-5: self-heal — if a stale worker's thread is actually DEAD (not just
        # slow), restart it. A stale-but-alive worker is left alone (it may be
        # mid-operation); restarting a live one would spawn a duplicate.
        restarted = []
        for name in stale:
            if name == self.__class__.__name__:
                continue  # can't be dead — we're running inside it
            worker = worker_registry.get_worker(name)
            thread = getattr(worker, "_thread", None) if worker else None
            if worker is not None and (thread is None or not thread.is_alive()):
                try:
                    worker.start()
                    restarted.append(name)
                    logger.critical(f"EtsyReceiptWorker: restarted dead worker {name}")
                except Exception as e:
                    logger.error(f"EtsyReceiptWorker: failed to restart dead worker {name}: {e}")

        try:
            from app.services.alert_service import AlertService
            msg = (
                f"Workers with no recent heartbeat: {', '.join(stale)}. "
                + (f"Auto-restarted: {', '.join(restarted)}. " if restarted else "")
                + "Check Railway logs."
            )
            AlertService().send_alert_sync("Stale worker heartbeat", msg, level="warning")
        except Exception as e:
            logger.warning(f"EtsyReceiptWorker: failed to send stale-heartbeat alert: {e}")

    # ── Daily backup tick (C-3) ───────────────────────────────────────────────

    def _maybe_backup(self):
        if not getattr(settings, "BACKUP_ENABLED", True):
            return
        state = self._load_state()
        now = int(datetime.now(timezone.utc).timestamp())
        interval = getattr(settings, "BACKUP_INTERVAL_HOURS", 24) * 3600
        if now - state.get("last_backup_at", 0) < interval:
            return

        from app.services.backup_service import BackupService
        svc = BackupService()
        report = svc.create_backup()
        state["last_backup_at"] = now

        # Weekly nag if offsite isn't configured — local-only backups don't
        # survive a volume failure.
        if not svc.offsite_configured():
            if now - state.get("last_backup_warn_at", 0) >= 7 * 24 * 3600:
                state["last_backup_warn_at"] = now
                try:
                    from app.services.alert_service import AlertService
                    AlertService().send_alert_sync(
                        "Offsite backup NOT configured",
                        "Backups are kept only on the Railway volume (last N zips) — a volume "
                        "failure would still lose everything. Configure BACKUP_S3_* (Cloudflare "
                        "R2 / Backblaze B2) for real off-box protection.",
                        level="warning",
                    )
                except Exception:
                    pass
        self._save_state(state)
        logger.info(f"EtsyReceiptWorker: backup tick done — {report}")

    def _maybe_spawn_winner_variant(self, task_id):
        """A-1: create ONE follow-up variant concept task seeded from a product
        that just sold. Capped per day, respects AUTONOMY_ENABLED."""
        try:
            if not settings.AUTONOMY_ENABLED or getattr(settings, "WINNER_VARIANTS_PER_DAY", 0) <= 0:
                return
            from app.services.autonomy_service import AutonomyService
            auto = AutonomyService()
            if not auto.can_create_winner_variant():
                return
            from app.core.product_formats import PRODUCT_FORMATS
            from app.services.task_service import TaskService
            from app.schemas.task import TaskCreate
            ts = TaskService()
            parent = ts.get_task(task_id)
            if not parent or parent.type not in PRODUCT_FORMATS:
                return
            title = (parent.output_data or {}).get("title") or (parent.metadata_ or {}).get("product_name") or "a proven seller"
            prompt = (
                f"Create a {parent.type.replace('_', ' ')} product that is a FRESH variation on a proven "
                f"seller ('{title}'): keep the appealing theme and style that made it sell, but make it a "
                f"clearly different, original design (not a copy). No brand/trademark references."
            )
            new_task = ts.create_task(TaskCreate(
                prompt=prompt, type=parent.type,
                metadata={"source": "winner_variant", "parent_task_id": task_id,
                          "product_name": f"{title} variation"},
            ))
            auto.record_winner_variant()
            logger.info(f"EtsyReceiptWorker: spawned winner-variant task {new_task.id} from sold task {task_id}")
        except Exception as e:
            logger.error(f"EtsyReceiptWorker: winner-variant spawn failed for {task_id}: {e}")

    def _maybe_cleanup_images(self):
        """Once per day, prune old generated images so the volume doesn't fill."""
        state = self._load_state()
        now = int(datetime.now(timezone.utc).timestamp())
        if now - state.get("last_image_cleanup_at", 0) < 24 * 3600:
            return
        from app.services.image_cleanup_service import ImageCleanupService
        report = ImageCleanupService().cleanup()
        state["last_image_cleanup_at"] = now
        self._save_state(state)
        logger.info(f"EtsyReceiptWorker: image-cleanup tick done — {report}")

    def _maybe_poll_listing_stats(self):
        """A-10: once per day, poll active-listing views/favorites and record
        them as analytics events."""
        state = self._load_state()
        now = int(datetime.now(timezone.utc).timestamp())
        if now - state.get("last_stats_poll_at", 0) < 24 * 3600:
            return
        from app.services.listing_stats_service import ListingStatsService
        report = ListingStatsService().poll_and_record()
        state["last_stats_poll_at"] = now
        self._save_state(state)
        logger.info(f"EtsyReceiptWorker: listing-stats poll done — {report}")

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
