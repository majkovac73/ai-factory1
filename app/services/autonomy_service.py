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
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.paths import get_data_dir
from config import settings

logger = logging.getLogger("ai-factory")


class SpendCapExceeded(RuntimeError):
    """5-2: raised by a provider when the day's spend has blown past the hard
    ceiling (MAX_DAILY_SPEND_USD * SPEND_CIRCUIT_BREAKER_MULT). A can_spend()
    check is advisory and can be raced by concurrent calls; this is the
    last-resort circuit breaker that actually stops money going out."""


class AutonomyService:
    # 5-1: the daily ledger is a read-modify-write on a single JSON file. A new
    # AutonomyService() is constructed per call, so the lock MUST be shared at
    # class scope (an instance lock would protect nothing). This serializes
    # concurrent in-process spend/task recording so updates can't clobber each
    # other; the write itself is atomic (os.replace) so a crash mid-write can't
    # leave a truncated ledger that reads back as $0 and re-opens the spend gate.
    _lock = threading.Lock()

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
        # 5-1: atomic write — serialize to a temp file in the same directory,
        # fsync-free but atomically renamed into place, so a reader never sees a
        # half-written ledger.
        p = self._state_path()
        tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, p)

    # ── Task cap ───────────────────────────────────────────────────────────────

    def can_create_task(self) -> bool:
        state = self._load()
        return state["tasks_created"] < settings.MAX_TASKS_PER_DAY

    def record_task_created(self):
        with self._lock:
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
        with self._lock:
            state = self._load()
            state["spend_usd"] = round(state["spend_usd"] + amount_usd, 6)
            self._save(state)
        logger.info(
            f"AutonomyService: ${amount_usd:.4f} recorded ({description}). "
            f"Daily total: ${state['spend_usd']:.4f}/${settings.MAX_DAILY_SPEND_USD:.2f}"
        )

        if state["spend_usd"] >= settings.MAX_DAILY_SPEND_USD:
            self._alert_cap_hit("spend", state["spend_usd"], settings.MAX_DAILY_SPEND_USD)

    def spend_today(self) -> float:
        """5-2: today's recorded spend (for the provider circuit breaker)."""
        return float(self._load().get("spend_usd", 0.0) or 0.0)

    def assert_within_circuit_breaker(self):
        """5-2: hard stop. can_spend() is advisory and racy; this raises
        SpendCapExceeded once the day's spend is past the ceiling so a provider
        physically refuses to make one more paid call. The multiplier gives
        headroom for in-flight calls that each individually passed can_spend()."""
        mult = float(getattr(settings, "SPEND_CIRCUIT_BREAKER_MULT", 1.5))
        ceiling = settings.MAX_DAILY_SPEND_USD * mult
        spent = self.spend_today()
        if spent >= ceiling:
            # 4-2: a retry loop past the ceiling would fire one Discord alert PER
            # refused call. Alert at most once per day (marker file), then just
            # raise silently on subsequent hits.
            if self._breaker_alert_once_per_day():
                self._alert_cap_hit("spend-circuit-breaker", round(spent, 4), round(ceiling, 4))
            raise SpendCapExceeded(
                f"Daily spend ${spent:.2f} has exceeded the circuit-breaker ceiling "
                f"${ceiling:.2f} (MAX_DAILY_SPEND_USD ${settings.MAX_DAILY_SPEND_USD:.2f} "
                f"x {mult}). Refusing further paid API calls today."
            )

    def _breaker_alert_once_per_day(self) -> bool:
        """4-2: True at most once per UTC day (marker file), so the circuit-breaker
        alert can't spam Discord on every refused call in a retry loop."""
        import time as _t
        try:
            marker = self._state_dir / "breaker_alert.json"
            last = 0
            if marker.exists():
                last = json.loads(marker.read_text(encoding="utf-8")).get("at", 0)
            if _t.time() - last < 86400:
                return False
            marker.write_text(json.dumps({"at": _t.time()}), encoding="utf-8")
            return True
        except Exception:
            return True  # if the marker can't be read/written, err toward alerting

    # ── Winner-variant cap (A-1) ────────────────────────────────────────────────

    def can_create_winner_variant(self) -> bool:
        state = self._load()
        return state.get("winner_variants", 0) < settings.WINNER_VARIANTS_PER_DAY

    def record_winner_variant(self):
        with self._lock:
            state = self._load()
            state["winner_variants"] = state.get("winner_variants", 0) + 1
            self._save(state)
        logger.info(
            f"AutonomyService: winner-variant recorded "
            f"({state['winner_variants']}/{settings.WINNER_VARIANTS_PER_DAY} today)"
        )

    def lifetime_spend(self) -> float:
        """D-6: total recorded spend across ALL daily ledgers (the per-day state
        files) — for the dashboard P&L tile."""
        total = 0.0
        try:
            for p in self._state_dir.glob("autonomy_state_*.json"):
                try:
                    total += float(json.loads(p.read_text(encoding="utf-8")).get("spend_usd", 0) or 0)
                except Exception:
                    continue
        except Exception:
            pass
        return round(total, 4)

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
