"""
AutonomyWorker — step 88.

Background thread that runs market-intelligence agents on a schedule and
routes new product ideas through the task-creation pipeline.

Kill switch: AUTONOMY_ENABLED=False (default) — the worker starts but
immediately exits its loop without doing anything. Set to True in Railway
env vars only when ready to let the system run autonomously.

Schedule: AUTONOMY_SCHEDULE_SECONDS=3600 (1 hour between runs).

What it does each cycle (when AUTONOMY_ENABLED=True):
  1. Calls AutonomyService to confirm daily limits not exceeded
  2. Runs TrendResearchAgent to surface a product opportunity
  3. Creates a new task via TaskService (same pipeline as manual tasks)
  4. Records the task + estimated spend in AutonomyService

AUTONOMY_ENABLED defaults to False. Do not set it to True until Maj
explicitly decides the autonomous loop is ready to run unsupervised.
"""
import logging
import threading
from typing import Optional

from app.services import worker_registry
from config import settings

logger = logging.getLogger("ai-factory")


class AutonomyWorker:
    def __init__(self, schedule_seconds: Optional[int] = None):
        self._schedule_seconds = schedule_seconds if schedule_seconds is not None else self._resolve_interval_seconds()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _resolve_interval_seconds() -> int:
        """AUTONOMY_INTERVAL_MINUTES (friendly Railway knob) wins when set;
        otherwise fall back to AUTONOMY_SCHEDULE_SECONDS. Floor of 60s."""
        minutes = getattr(settings, "AUTONOMY_INTERVAL_MINUTES", None)
        if minutes:
            return max(60, int(minutes) * 60)
        return settings.AUTONOMY_SCHEDULE_SECONDS

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("AutonomyWorker: start() called but worker already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="AutonomyWorker"
        )
        self._thread.start()
        if settings.AUTONOMY_ENABLED:
            logger.info(
                f"AutonomyWorker: started — AUTONOMY_ENABLED=True, one product every "
                f"{self._schedule_seconds}s ({self._schedule_seconds / 60:.0f} min); "
                f"set AUTONOMY_INTERVAL_MINUTES in Railway to change"
            )
        else:
            logger.info("AutonomyWorker: started — AUTONOMY_ENABLED=False (kill switch active, no tasks will be created)")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("AutonomyWorker: stopped")

    def _run_loop(self):
        try:
            while not self._stop_event.is_set():
                worker_registry.record_heartbeat("AutonomyWorker")

                if settings.AUTONOMY_ENABLED:
                    try:
                        self._run_cycle()
                    except Exception as e:
                        logger.error(f"AutonomyWorker: error in cycle: {e}")

                self._stop_event.wait(self._schedule_seconds)
        finally:
            if not self._stop_event.is_set():
                logger.critical("AutonomyWorker: thread exiting unexpectedly")
                try:
                    from app.services.alert_service import AlertService
                    AlertService().send_alert_sync(
                        "AutonomyWorker thread died",
                        "AutonomyWorker exited its run loop without being stopped.",
                        level="error",
                    )
                except Exception:
                    pass

    def _run_cycle(self):
        from app.services.autonomy_service import AutonomyService
        from app.services.task_service import TaskService
        from app.schemas.task import TaskCreate

        autonomy = AutonomyService()

        if not autonomy.can_create_task():
            logger.info("AutonomyWorker: daily task cap reached, skipping cycle")
            return

        # P0-13: reserve against a realistic worst-case cycle cost before
        # starting (a pdf_planner is ~$0.40-0.80 once PDF pages + QA + mockups +
        # remakes are counted). Actual spend is recorded per-image at the
        # provider choke point + per vision-QA call, so the ledger reflects
        # reality instead of a flat guess.
        estimated_max = 0.80
        if not autonomy.can_spend(estimated_max):
            logger.info(
                f"AutonomyWorker: daily spend cap would be exceeded "
                f"(estimated ${estimated_max:.2f}), skipping cycle"
            )
            return

        logger.info("AutonomyWorker: running autonomous cycle")

        # Import here to keep worker startup fast
        from app.agents.trend_research_agent import TrendResearchAgent

        agent = TrendResearchAgent()
        opportunity = agent.run()

        if not opportunity:
            logger.info("AutonomyWorker: TrendResearchAgent returned no opportunity, skipping")
            return

        product_name = opportunity.get("product_name") or "Product"
        product_format = opportunity.get("product_format") or "single_print"
        description = opportunity.get("description", "")
        target_audience = opportunity.get("target_audience", "")
        page_count = opportunity.get("page_count")

        prompt_parts = [f"Create a {product_format.replace('_', ' ')} product: {product_name}."]
        if description:
            prompt_parts.append(description)
        if target_audience:
            prompt_parts.append(f"Target audience: {target_audience}.")
        prompt = " ".join(prompt_parts)

        logger.info(f"AutonomyWorker: creating task for product: {product_name[:80]} (format={product_format})")

        metadata = {"source": "autonomy_worker", "product_name": product_name}
        if page_count:
            metadata["page_count"] = page_count
        # A-2: carry the Etsy market data (real median price + winning titles)
        # so the listing stage grounds pricing and the executor grounds SEO.
        market = opportunity.get("market")
        if market:
            metadata["market"] = market
            if market.get("top_titles"):
                metadata["seo_context"] = market["top_titles"]
        # B-4: carry a text-led flag + exact words so the design stage renders
        # the typography deterministically (no garbled Seedream text).
        if opportunity.get("text_led") and opportunity.get("display_text"):
            metadata["text_led"] = True
            metadata["display_text"] = opportunity["display_text"]

        task_service = TaskService()
        task = task_service.create_task(TaskCreate(
            prompt=prompt,
            type=product_format,
            metadata=metadata,
        ))

        autonomy.record_task_created()
        # P0-13: no flat text-LLM record here — real spend (images + vision-QA)
        # is recorded as it happens downstream, so this no longer double-counts.

        logger.info(f"AutonomyWorker: created task {task.id} for product: {product_name[:80]}")
