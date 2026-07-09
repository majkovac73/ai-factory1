"""
MarketingRefreshWorker — recurring re-promotion of existing published products.

Same background-thread pattern as AutonomyWorker/EtsyReceiptWorker: heartbeat
via worker_registry, kill switch, death-alert in a finally block.

Kill switch: MARKETING_REFRESH_ENABLED=False (default) — the worker starts but
does nothing until Maj explicitly enables it in Railway env vars. Same
safety-default philosophy as AUTONOMY_ENABLED / AUTO_PUBLISH_LISTINGS.

Schedule: MARKETING_REFRESH_SCHEDULE_SECONDS (default 21600 = every 6 hours).
Per cycle it re-promotes up to MARKETING_REFRESH_MAX_POSTS_PER_CYCLE of the
least-recently-marketed published products, using ALREADY-GENERATED assets (no
new image generation). The only spend is one small optional caption-rewrite LLM
call per post.
"""
import logging
import threading
from typing import Optional

from app.services import worker_registry
from config import settings

logger = logging.getLogger("ai-factory")


class MarketingRefreshWorker:
    def __init__(self, schedule_seconds: Optional[int] = None, service=None):
        self._schedule_seconds = schedule_seconds or settings.MARKETING_REFRESH_SCHEDULE_SECONDS
        self._service = service
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("MarketingRefreshWorker: start() called but worker already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MarketingRefreshWorker")
        self._thread.start()
        if settings.MARKETING_REFRESH_ENABLED:
            logger.info(
                f"MarketingRefreshWorker: started — MARKETING_REFRESH_ENABLED=True, "
                f"every {self._schedule_seconds}s, up to {settings.MARKETING_REFRESH_MAX_POSTS_PER_CYCLE} post(s)/cycle"
            )
        else:
            logger.info("MarketingRefreshWorker: started — MARKETING_REFRESH_ENABLED=False (kill switch active, no posts)")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("MarketingRefreshWorker: stopped")

    def _run_loop(self):
        try:
            while not self._stop_event.is_set():
                worker_registry.record_heartbeat("MarketingRefreshWorker")

                if settings.MARKETING_REFRESH_ENABLED:
                    try:
                        self._run_cycle()
                    except Exception as e:
                        logger.error(f"MarketingRefreshWorker: error in cycle: {e}")

                self._stop_event.wait(self._schedule_seconds)
        finally:
            if not self._stop_event.is_set():
                logger.critical("MarketingRefreshWorker: thread exiting unexpectedly")
                try:
                    from app.services.alert_service import AlertService
                    AlertService().send_alert_sync(
                        "MarketingRefreshWorker thread died",
                        "MarketingRefreshWorker exited its run loop without being stopped.",
                        level="error",
                    )
                except Exception:
                    pass

    def _run_cycle(self):
        from app.services.marketing_refresh_service import MarketingRefreshService

        service = self._service or MarketingRefreshService()
        channels = self._available_channels()
        if not channels:
            logger.info("MarketingRefreshWorker: no marketing channels available, skipping cycle")
            return

        result = service.run_cycle(channels, max_posts=settings.MARKETING_REFRESH_MAX_POSTS_PER_CYCLE)
        logger.info(f"MarketingRefreshWorker: cycle complete — {result.get('posted', 0)} post(s)")

    def _available_channels(self) -> list:
        """Channels with a usable connection. Tumblr is primary; Pinterest is
        included only if it has been authorized (token row present)."""
        channels = []
        try:
            from app.db.database import SessionLocal
            from app.models.tumblr_token import TumblrToken
            from app.models.pinterest_token import PinterestToken

            db = SessionLocal()
            try:
                has_tumblr = settings.TUMBLR_CONSUMER_KEY and db.query(TumblrToken).first() is not None
                has_pinterest = db.query(PinterestToken).first() is not None
            finally:
                db.close()

            if has_tumblr:
                from app.marketing.tumblr_channel import TumblrChannel
                channels.append(TumblrChannel())
            if has_pinterest:
                from app.marketing.pinterest_channel import PinterestChannel
                channels.append(PinterestChannel())
        except Exception as e:
            logger.warning(f"MarketingRefreshWorker: could not build channel list: {e}")
        return channels
