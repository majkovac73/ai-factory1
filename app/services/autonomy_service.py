"""
AutonomyService — step 88.

Enforces hard daily limits before the AutonomyWorker can create new tasks
or spend money via the LLM/image APIs.

Limits (loaded from settings, set in Railway env vars):
  MAX_TASKS_PER_DAY = 10
  MAX_DAILY_SPEND_USD = 5.00

State lives in <data_dir>/autonomy_state_<YYYY-MM-DD>.json.
A new file is created each UTC day; yesterday's file is left for audit.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.paths import get_data_dir
from config import settings

logger = logging.getLogger("ai-factory")


class AutonomyService:
    def __init__(self):
        self._state_dir = get_data_dir()
        self._state_dir.mkdir(parents=True, exist_ok=True)

    # ── State file ─────────────────────────────────────────────────────────────

    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _state_path(self) -> Path:
        return self._state_dir / f"autonomy_state_{self._today()}.json"

    def _load(self) -> dict:
        p = self._state_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"tasks_created": 0, "spend_usd": 0.0}

    def _save(self, state: dict):
        self._state_path().write_text(json.dumps(state), encoding="utf-8")

    # ── Task cap ───────────────────────────────────────────────────────────────

    def can_create_task(self) -> bool:
        state = self._load()
        return state["tasks_created"] < settings.MAX_TASKS_PER_DAY

    def record_task_created(self):
        state = self._load()
        state["tasks_created"] += 1
        self._save(state)
        logger.info(
            f"AutonomyService: task recorded ({state['tasks_created']}/{settings.MAX_TASKS_PER_DAY} today)"
        )

        if state["tasks_created"] >= settings.MAX_TASKS_PER_DAY:
            self._alert_cap_hit("task", state["tasks_created"], settings.MAX_TASKS_PER_DAY)

    # ── Spend cap ──────────────────────────────────────────────────────────────

    def can_spend(self, amount_usd: float) -> bool:
        state = self._load()
        return (state["spend_usd"] + amount_usd) <= settings.MAX_DAILY_SPEND_USD

    def record_spend(self, amount_usd: float, description: str = ""):
        state = self._load()
        state["spend_usd"] = round(state["spend_usd"] + amount_usd, 6)
        self._save(state)
        logger.info(
            f"AutonomyService: ${amount_usd:.4f} recorded ({description}). "
            f"Daily total: ${state['spend_usd']:.4f}/${settings.MAX_DAILY_SPEND_USD:.2f}"
        )

        if state["spend_usd"] >= settings.MAX_DAILY_SPEND_USD:
            self._alert_cap_hit("spend", state["spend_usd"], settings.MAX_DAILY_SPEND_USD)

    # ── Winner-variant cap (A-1) ────────────────────────────────────────────────

    def can_create_winner_variant(self) -> bool:
        state = self._load()
        return state.get("winner_variants", 0) < settings.WINNER_VARIANTS_PER_DAY

    def record_winner_variant(self):
        state = self._load()
        state["winner_variants"] = state.get("winner_variants", 0) + 1
        self._save(state)
        logger.info(
            f"AutonomyService: winner-variant recorded "
            f"({state['winner_variants']}/{settings.WINNER_VARIANTS_PER_DAY} today)"
        )

    # ── Read-only status ───────────────────────────────────────────────────────

    def daily_status(self) -> dict:
        state = self._load()
        return {
            "date": self._today(),
            "tasks_created": state["tasks_created"],
            "max_tasks_per_day": settings.MAX_TASKS_PER_DAY,
            "spend_usd": state["spend_usd"],
            "max_daily_spend_usd": settings.MAX_DAILY_SPEND_USD,
        }

    # ── Internal alert helper ──────────────────────────────────────────────────

    def _alert_cap_hit(self, cap_type: str, current, limit):
        try:
            from app.services.alert_service import AlertService
            AlertService().send_alert_sync(
                f"Daily {cap_type} cap hit",
                f"AI Factory has reached the daily {cap_type} limit "
                f"({current}/{limit}). No further autonomous work will run today.",
                level="warning",
            )
        except Exception as e:
            logger.warning(f"AutonomyService: failed to send cap alert: {e}")
