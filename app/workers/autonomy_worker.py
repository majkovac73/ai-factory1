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
        self._schedule_seconds = schedule_seconds or settings.AUTONOMY_SCHEDULE_SECONDS
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

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
                f"AutonomyWorker: started — AUTONOMY_ENABLED=True, "
                f"schedule every {self._schedule_seconds}s"
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

        # Gate check uses worst-case estimate so the daily cap is never silently
        # exceeded — but we only record the text-LLM portion ($0.05) upfront.
        # PipelineOrchestrator records the image-gen portion ($0.20) on success,
        # so a failed pipeline only burns $0.05 instead of the full $0.30.
        estimated_max = 0.30
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

        concept = opportunity.get("concept") or opportunity.get("title") or str(opportunity)
        logger.info(f"AutonomyWorker: creating task for concept: {concept[:80]}")

        task_service = TaskService()
        task = task_service.create_task(TaskCreate(
            prompt=concept,
            type="general",
            metadata={"source": "autonomy_worker"},
        ))

        autonomy.record_task_created()
        autonomy.record_spend(0.05, f"text-LLM cycle task={task.id[:8]}")

        logger.info(f"AutonomyWorker: created task {task.id} for concept: {concept[:80]}")
