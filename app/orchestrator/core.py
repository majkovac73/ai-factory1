import logging

from app.schemas.enums import TaskStatus
from app.services.task_service import TaskService
from app.services.task_processor import TaskProcessor

logger = logging.getLogger("ai-factory")


class Orchestrator:
    """
    Coordinates processing across multiple tasks. Sits above TaskProcessor
    (which handles a single task's lifecycle) and is responsible for
    deciding which tasks to run and in what order.

    This step runs tasks sequentially and in-process. Step 37/38 will
    introduce a real task queue and background worker; when that happens,
    this class's run_pending() logic is what the worker will call per task,
    rather than looping over all of them synchronously here.
    """

    def __init__(self):
        self.task_service = TaskService()
        self.task_processor = TaskProcessor()

    def run_task(self, task_id: str):
        """Process a single task by ID and return its final state."""
        return self.task_processor.process(task_id)

    def run_pending(self):
        """
        Find all tasks currently in NEW status and process them one by one.
        Returns a summary of what succeeded and what failed, so a single
        bad task doesn't stop the rest of the batch from running.
        """
        all_tasks = self.task_service.list_tasks()
        pending = [t for t in all_tasks if t.status == TaskStatus.NEW.value]

        results = {
            "total_pending": len(pending),
            "succeeded": [],
            "failed": [],
        }

        logger.info(f"Orchestrator: found {len(pending)} pending task(s) to run")

        for task in pending:
            try:
                self.task_processor.process(task.id)
                results["succeeded"].append(task.id)
            except Exception as e:
                logger.error(f"Orchestrator: task {task.id} failed during batch run: {e}")
                results["failed"].append({"task_id": task.id, "error": str(e)})

        logger.info(
            f"Orchestrator: batch run complete — "
            f"{len(results['succeeded'])} succeeded, {len(results['failed'])} failed"
        )

        return results