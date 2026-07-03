import logging

from app.core.agents.planner import PlannerAgent
from app.core.agents.executor import ExecutorAgent
from app.core.agents.qa import QAAgent
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
        task = self.task_service.get_task(task_id)
        planner = PlannerAgent()

        task_type = task.type or "general"
        plan = planner.create_plan(task_type, task.prompt)

        self.task_service.save_plan(task_id, plan)
        logger.info(f"Task {task_id}: plan created with {len(plan.get('steps', []))} step(s)")

    def _execute(self, task_id: str):
        task = self.task_service.get_task(task_id)
        executor = ExecutorAgent()

        plan = task.metadata_ or {}
        steps = plan.get("steps", [])

        if not steps:
            logger.warning(f"Task {task_id}: no plan steps found, executing prompt directly")
            steps = [task.prompt]

        context = task.prompt or ""
        outputs = []

        for i, step in enumerate(steps, start=1):
            logger.info(f"Task {task_id}: executing step {i}/{len(steps)}")
            step_output = executor.execute_step(step, context)
            outputs.append(step_output)
            context += f"\n{step_output}"

        combined_result = "\n\n".join(outputs)
        self.task_service.save_result(task_id, combined_result)
        logger.info(f"Task {task_id}: execution complete, {len(outputs)} step(s) run")

    def _qa(self, task_id: str) -> bool:
        task = self.task_service.get_task(task_id)
        qa_agent = QAAgent()

        if not task.result:
            self.task_service.save_qa_result(task_id, output_data=None, error_message="No result to validate")
            logger.warning(f"Task {task_id}: QA failed, no result present")
            return False

        validation = qa_agent.review(task.result)

        if validation.get("valid"):
            self.task_service.save_qa_result(task_id, output_data=validation["data"], error_message=None)
            logger.info(f"Task {task_id}: QA passed")
            return True

        error = validation.get("error", "QA validation failed")
        self.task_service.save_qa_result(task_id, output_data=None, error_message=error)
        logger.warning(f"Task {task_id}: QA failed - {error}")
        return False