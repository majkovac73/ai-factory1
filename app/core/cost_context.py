"""
Cost attribution context (Audit 2026-07-20 #4).

The daily spend ledger (AutonomyService) knows the TOTAL dollars spent per day
but not WHICH task/format they were spent on — so there is no per-product cost,
and therefore no per-product profit. Plumbing a task_id through every provider
call (image gen, vision-QA, text LLM) would touch dozens of signatures; instead
the pipeline orchestrator sets the current task_id in a context variable while it
processes a task, and the provider choke points read it to emit a `cost_incurred`
analytics event tagged with that task.

contextvars propagate into the async image call (asyncio.run copies the current
context) and are per-thread, so concurrent task processing in different worker
threads attributes cost to the right task.
"""
import contextvars
import logging
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("ai-factory")

_current_task_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_task_id", default=None
)


@contextmanager
def cost_attribution(task_id: Optional[str]):
    """Attribute all `cost_incurred` events emitted within the block to task_id."""
    token = _current_task_id.set(task_id)
    try:
        yield
    finally:
        _current_task_id.reset(token)


def current_task_id() -> Optional[str]:
    return _current_task_id.get()


def record_cost(usd: float, use_case: str, provider: str = "", model: str = "") -> None:
    """Emit a `cost_incurred` analytics event for the current task. Best-effort:
    a ledger/analytics failure must never break the paid work that just happened.

    entity_type='task', entity_id=<current task_id or 'unattributed'>, value=usd,
    payload={provider, model, use_case}. Reconciles (±) with the daily
    autonomy_state spend totals per #4's verification step."""
    try:
        from app.services.analytics_service import AnalyticsService
        AnalyticsService().record_event(
            event_type="cost_incurred",
            entity_type="task",
            entity_id=(_current_task_id.get() or "unattributed"),
            value=float(usd),
            payload={"provider": provider, "model": model, "use_case": use_case},
        )
    except Exception as e:
        logger.warning(f"cost_context.record_cost failed ({use_case}): {e}")
