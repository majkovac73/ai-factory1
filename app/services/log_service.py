import json
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