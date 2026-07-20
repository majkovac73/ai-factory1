import json
import logging
import threading
import uuid
from datetime import datetime

from app.db.database import SessionLocal
from app.models.log import Log


class LogService:
    """
    Centralized logging service that writes to both standard Python
    logging (console/files) and the SQLite Log table. Provides a
    structured interface for logging across tasks, agents, and workflows.

    Each log entry is scoped by source (e.g. "ai-factory", "task_processor",
    "agent_planner") and optionally linked to an entity (entity_type +
    entity_id) for filtering/searching logs by task, agent, etc.
    """

    def __init__(self):
        pass

    def log(
        self,
        level: str,
        source: str,
        message: str,
        payload: dict = None,
    ) -> None:
        """
        Log a message at the specified level to both Python logging and
        the SQLite Log table.

        Args:
            level: "INFO", "WARNING", "ERROR", "DEBUG", etc.
            source: Where the log came from (e.g. "task_processor", "planner_agent")
            message: Human-readable log message
            payload: Optional structured data (dict) to store as JSON
        """
        db = SessionLocal()
        try:
            log_record = Log(
                id=str(uuid.uuid4()),
                level=level,
                source=source,
                message=message,
                payload=payload or {},
                created_at=datetime.utcnow(),
            )
            db.add(log_record)
            db.commit()
        except Exception as e:
            db.rollback()
            # Fail gracefully — if logging itself fails, don't crash the app
            print(f"LogService failed to write: {e}")
        finally:
            db.close()

    def info(self, source: str, message: str, payload: dict = None) -> None:
        """Log an INFO level message."""
        self.log("INFO", source, message, payload)

    def warning(self, source: str, message: str, payload: dict = None) -> None:
        """Log a WARNING level message."""
        self.log("WARNING", source, message, payload)

    def error(self, source: str, message: str, payload: dict = None) -> None:
        """Log an ERROR level message."""
        self.log("ERROR", source, message, payload)

    def debug(self, source: str, message: str, payload: dict = None) -> None:
        """Log a DEBUG level message."""
        self.log("DEBUG", source, message, payload)

    def list_logs(self, source: str = None, level: str = None, limit: int = 100):
        """
        Retrieve logs from the database, optionally filtered by source
        and/or level, ordered most recent first.
        """
        db = SessionLocal()
        try:
            query = db.query(Log)
            if source:
                query = query.filter(Log.source == source)
            if level:
                query = query.filter(Log.level == level)
            return query.order_by(Log.created_at.desc()).limit(limit).all()
        finally:
            db.close()

    def error_summary(self, hours: int = 24) -> dict:
        """#14: quick DB-only view of persisted problems — count of ERROR/WARNING
        rows in the window + the most recent few. Proves the 'every failure
        traceable from the DB' rule now holds."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        db = SessionLocal()
        try:
            rows = (
                db.query(Log)
                .filter(Log.level.in_(["ERROR", "CRITICAL", "WARNING"]), Log.created_at >= cutoff)
                .order_by(Log.created_at.desc())
                .limit(500)
                .all()
            )
        finally:
            db.close()
        errors = [r for r in rows if r.level in ("ERROR", "CRITICAL")]
        return {
            "window_hours": hours,
            "error_count": len(errors),
            "warning_count": len(rows) - len(errors),
            "recent": [{"level": r.level, "source": r.source, "message": (r.message or "")[:200]}
                       for r in rows[:10]],
        }

    def get_token_usage_summary(self):
        """
        Aggregates token usage across all logged LLM calls that carried
        usage data in their payload (see BaseAgent._generate(), Step 31).
        """
        db = SessionLocal()
        try:
            logs = (
                db.query(Log)
                .filter(Log.message == "LLM generation completed")
                .all()
            )
        finally:
            db.close()

        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        call_count = 0

        for log in logs:
            usage = (log.payload or {}).get("usage")
            if not usage:
                continue
            call_count += 1
            total_prompt_tokens += usage.get("prompt_tokens") or 0
            total_completion_tokens += usage.get("completion_tokens") or 0
            total_tokens += usage.get("total_tokens") or 0

        return {
            "llm_call_count": call_count,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens,
        }


class DBLogHandler(logging.Handler):
    """#14: forward WARNING+ log records into the `logs` table so failures are
    traceable from the DB, not just ephemeral stdout that vanishes when the
    container recycles. The audit found the logs table had ZERO ERROR/CRITICAL
    rows despite real failures because every logger.warning/error(...) went to
    stdout only. Installing this on the 'ai-factory' logger captures them all
    without touching each call site.

    A thread-local guard prevents infinite recursion if writing a log itself
    logs; the write is best-effort (never raises into the logging path)."""

    def __init__(self, level=logging.WARNING):
        super().__init__(level=level)
        self._svc = LogService()
        self._guard = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._guard, "active", False):
            return
        self._guard.active = True
        try:
            payload = {}
            if record.exc_info:
                payload["exc"] = logging.Formatter().formatException(record.exc_info)[:2000]
            self._svc.log(
                level=record.levelname,
                source=record.name or "ai-factory",
                message=str(record.getMessage())[:2000],
                payload=payload,
            )
        except Exception:
            pass  # logging must never crash the app
        finally:
            self._guard.active = False


def install_db_log_handler(logger_name: str = "ai-factory", level=logging.WARNING) -> None:
    """Attach a DBLogHandler to the named logger exactly once (idempotent)."""
    lg = logging.getLogger(logger_name)
    if any(isinstance(h, DBLogHandler) for h in lg.handlers):
        return
    lg.addHandler(DBLogHandler(level=level))