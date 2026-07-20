"""
ExecutionLogService (Audit 2026-07-20 #15) — persist per-step pipeline provenance.

The `task_steps` and `agent_executions` tables existed but had 0 rows despite 147
completed tasks, so there was no record of which stage did what, whether it
succeeded/was skipped/blocked, or what the run cost — routing and cost were not
auditable. This writes one TaskStep row per pipeline stage (from the orchestrator's
per-stage report) plus one AgentExecution summary row per run (with the task's
attributed cost), so per-stage outcome + cost is queryable from the DB.
"""
import logging
import uuid
from datetime import datetime

logger = logging.getLogger("ai-factory")


def _status_of(outcome: dict) -> str:
    """Derive a step status from the orchestrator's per-stage report entry."""
    if not isinstance(outcome, dict):
        return "success"
    if "skipped" in outcome:
        return "skipped"
    if outcome.get("blocked") or outcome.get("error") or outcome.get("ok") is False:
        return "failed"
    return "success"


class ExecutionLogService:
    def record_pipeline_run(self, task_id: str, report: dict) -> dict:
        """#15: persist per-stage TaskStep rows + an AgentExecution summary for a
        completed post-completion pipeline run. Best-effort — provenance logging
        must never break the pipeline. Returns a small summary."""
        from app.db.database import SessionLocal
        from app.models.task_step import TaskStep
        from app.models.agent_execution import AgentExecution

        stages = (report or {}).get("stages") or {}
        cost = self._task_cost(task_id)
        blocked = bool((report or {}).get("blocked"))
        written = 0
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            for step_name, outcome in stages.items():
                db.add(TaskStep(
                    id=str(uuid.uuid4()),
                    task_id=task_id,
                    step_name=str(step_name)[:120],
                    output_data=outcome if isinstance(outcome, dict) else {"value": outcome},
                    status=_status_of(outcome),
                    finished_at=now,
                    error=(str(outcome.get("error"))[:500] if isinstance(outcome, dict) and outcome.get("error") else None),
                ))
                written += 1
            db.add(AgentExecution(
                id=str(uuid.uuid4()),
                task_id=task_id,
                agent_name="PipelineOrchestrator",
                role="post_completion_pipeline",
                output={"stages": list(stages.keys()), "cost_usd": cost,
                        "blocked": blocked, "listing_id": (report or {}).get("stages", {}).get("create_listing", {}).get("listing_id")},
                model_used=None,
                status="blocked" if blocked else "success",
            ))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"ExecutionLogService: could not record pipeline run for {task_id}: {e}")
        finally:
            db.close()
        return {"steps_written": written, "cost_usd": cost, "blocked": blocked}

    @staticmethod
    def _task_cost(task_id: str) -> float:
        try:
            from app.services.revenue_service import RevenueService
            return float(RevenueService().get_total_cost(task_id).get("total_cost", 0.0) or 0.0)
        except Exception:
            return 0.0
