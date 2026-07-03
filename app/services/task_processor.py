import logging

from app.schemas.enums import TaskStatus
from app.services.task_service import TaskService

logger = logging.getLogger("ai-factory")


class TaskProcessor:
    """
    Drives a single task through the full lifecycle:
    NEW -> PLANNED -> RUNNING -> QA -> DONE

    Each stage currently does placeholder work. Steps 15-17 (Planner,
    Executor, QA modules) will replace the placeholder bodies of
    _plan(), _execute(), and _qa() with real logic without needing to
    change the control flow here.
    """

    def __init__(self):
        self.task_service = TaskService()

    def process(self, task_id: str):
        task = self.task_service.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        try:
            self._advance(task_id, TaskStatus.PLANNED.value)
            self._plan(task_id)

            self._advance(task_id, TaskStatus.RUNNING.value)
            self._execute(task_id)

            self._advance(task_id, TaskStatus.QA.value)
            qa_passed = self._qa(task_id)

            if qa_passed:
                self._advance(task_id, TaskStatus.DONE.value)
            else:
                # Send back for rework; QA -> RUNNING is a legal transition
                self._advance(task_id, TaskStatus.RUNNING.value)
                logger.warning(f"Task {task_id} failed QA, sent back to RUNNING")

            return self.task_service.get_task(task_id)

        except Exception as e:
            logger.error(f"Task {task_id} processing failed: {e}")
            try:
                self._advance(task_id, TaskStatus.FAILED.value)
            except Exception as inner_e:
                logger.error(f"Task {task_id} could not be marked FAILED: {inner_e}")
            raise

    def _advance(self, task_id: str, new_status: str):
        self.task_service.update_status(task_id, new_status)
        logger.info(f"Task {task_id} -> {new_status}")

    def _plan(self, task_id: str):
        # Placeholder for Step 15 (Planner module)
        logger.info(f"Task {task_id}: planning (placeholder)")

    def _execute(self, task_id: str):
        # Placeholder for Step 16 (Executor module)
        logger.info(f"Task {task_id}: executing (placeholder)")

    def _qa(self, task_id: str) -> bool:
        # Placeholder for Step 17 (QA module)
        # Returns True (pass) for now so tasks always complete during this step.
        logger.info(f"Task {task_id}: running QA (placeholder)")
        return True