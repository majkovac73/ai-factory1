import logging

from app.core.agents.planner import PlannerAgent
from app.core.agents.executor import ExecutorAgent
from app.core.agents.qa import QAAgent
from app.core.agents.fixer import FixerAgent
from app.agents.roles import get_role_for_task_type
from app.schemas.enums import TaskStatus
from app.services.task_service import TaskService
from app.services.log_service import LogService

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
        self.log_service = LogService()

    MAX_QA_RETRIES = 3

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

            attempts = 0
            while not qa_passed and attempts < self.MAX_QA_RETRIES:
                attempts += 1
                self.task_service.increment_retry_count(task_id)
                retry_msg = (
                    f"Task {task_id} failed QA, retrying execution "
                    f"(attempt {attempts}/{self.MAX_QA_RETRIES})"
                )
                logger.warning(retry_msg)
                self.log_service.warning(
                    source="TaskProcessor",
                    message=retry_msg,
                    payload={"task_id": task_id, "attempt": attempts, "max_retries": self.MAX_QA_RETRIES},
                )

                self._advance(task_id, TaskStatus.RUNNING.value)
                self._execute(task_id)

                self._advance(task_id, TaskStatus.QA.value)
                qa_passed = self._qa(task_id)

            if qa_passed:
                self._advance(task_id, TaskStatus.DONE.value)
            else:
                fail_msg = f"Task {task_id} failed QA after {self.MAX_QA_RETRIES} retries, marking FAILED"
                logger.error(fail_msg)
                self.log_service.error(
                    source="TaskProcessor",
                    message=fail_msg,
                    payload={"task_id": task_id, "retries_used": attempts},
                )
                self._advance(task_id, TaskStatus.FAILED.value)

            return self.task_service.get_task(task_id)

        except Exception as e:
            error_msg = f"Task {task_id} processing failed: {e}"
            logger.error(error_msg)
            self.log_service.error(
                source="TaskProcessor",
                message=error_msg,
                payload={"task_id": task_id, "error": str(e)},
            )
            try:
                self._advance(task_id, TaskStatus.FAILED.value)
            except Exception as inner_e:
                inner_msg = f"Task {task_id} could not be marked FAILED: {inner_e}"
                logger.error(inner_msg)
                self.log_service.error(
                    source="TaskProcessor",
                    message=inner_msg,
                    payload={"task_id": task_id, "error": str(inner_e)},
                )
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

    MAX_REPAIR_ATTEMPTS = 2

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

        # Validation failed - attempt targeted repair before giving up
        current_output = task.result
        error = validation.get("error", "QA validation failed")

        for attempt in range(1, self.MAX_REPAIR_ATTEMPTS + 1):
            logger.warning(f"Task {task_id}: QA failed - {error}. Attempting repair {attempt}/{self.MAX_REPAIR_ATTEMPTS}")
            self.log_service.warning(
                source="TaskProcessor",
                message=f"QA failed, attempting repair {attempt}/{self.MAX_REPAIR_ATTEMPTS}",
                payload={"task_id": task_id, "error": error},
            )

            fixer = FixerAgent()
            critique = {
                "valid": False,
                "score": 0,
                "issues": [error],
                "recommendation": "Fix the JSON so it matches the required schema exactly.",
            }
            role = get_role_for_task_type(task.type or "general")

            try:
                fixed_output = fixer.improve(current_output, critique, task.type or "general", task.prompt, role)
            except Exception as e:
                logger.error(f"Task {task_id}: repair attempt {attempt} failed to generate: {e}")
                break

            revalidation = qa_agent.review(fixed_output)

            if revalidation.get("valid"):
                self.task_service.save_result(task_id, fixed_output)
                self.task_service.save_qa_result(task_id, output_data=revalidation["data"], error_message=None)
                logger.info(f"Task {task_id}: QA passed after repair (attempt {attempt})")
                return True

            current_output = fixed_output
            error = revalidation.get("error", "QA validation failed after repair")

        self.task_service.save_qa_result(task_id, output_data=None, error_message=error)
        logger.warning(f"Task {task_id}: QA failed after repair attempts - {error}")
        return False