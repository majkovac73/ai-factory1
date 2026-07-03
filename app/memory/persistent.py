from typing import Any, List, Optional

from app.db.database import SessionLocal
from app.memory.base import MemoryInterface
from app.models.memory import Memory


class PersistentMemory(MemoryInterface):
    """
    SQLite-backed implementation of MemoryInterface, using the Memory
    SQLAlchemy model. Data survives process restarts. Each call opens
    and closes its own session, matching the pattern used elsewhere in
    the app (e.g. TaskService), rather than holding a session open for
    the lifetime of this object.
    """

    def add(self, entity_type: str, entity_id: str, key: str, value: Any) -> None:
        db = SessionLocal()
        try:
            existing = (
                db.query(Memory)
                .filter(
                    Memory.entity_type == entity_type,
                    Memory.entity_id == entity_id,
                    Memory.memory_key == key,
                )
                .first()
            )

            if existing:
                existing.memory_value = value
            else:
                record = Memory(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    memory_key=key,
                    memory_value=value,
                )
                db.add(record)

            db.commit()
        finally:
            db.close()

    def get(self, entity_type: str, entity_id: str, key: str) -> Optional[Any]:
        db = SessionLocal()
        try:
            record = (
                db.query(Memory)
                .filter(
                    Memory.entity_type == entity_type,
                    Memory.entity_id == entity_id,
                    Memory.memory_key == key,
                )
                .first()
            )
            return record.memory_value if record else None
        finally:
            db.close()

    def get_all(self, entity_type: str, entity_id: str) -> List[Any]:
        db = SessionLocal()
        try:
            records = (
                db.query(Memory)
                .filter(
                    Memory.entity_type == entity_type,
                    Memory.entity_id == entity_id,
                )
                .order_by(Memory.created_at.asc())
                .all()
            )
            return [r.memory_value for r in records]
        finally:
            db.close()

    def clear(self, entity_type: str, entity_id: str) -> None:
        db = SessionLocal()
        try:
            db.query(Memory).filter(
                Memory.entity_type == entity_type,
                Memory.entity_id == entity_id,
            ).delete()
            db.commit()
        finally:
            db.close()