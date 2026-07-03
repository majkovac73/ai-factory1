from abc import ABC, abstractmethod
from typing import Any, List, Optional


class MemoryInterface(ABC):
    """
    Abstract contract for agent memory backends. Concrete implementations
    (short-term/in-process, persistent/SQLite, etc.) must implement all
    of these methods. Agents interact only with this interface, never
    with a specific backend directly, so the backend can be swapped
    without changing agent code.

    All memory is scoped by entity_type + entity_id (e.g. entity_type=
    "task", entity_id=<task_id>), matching the Memory SQLAlchemy model.
    """

    @abstractmethod
    def add(self, entity_type: str, entity_id: str, key: str, value: Any) -> None:
        """Store a value under a given key, scoped to an entity."""
        raise NotImplementedError

    @abstractmethod
    def get(self, entity_type: str, entity_id: str, key: str) -> Optional[Any]:
        """Retrieve a single value by key for a given entity. Returns None if not found."""
        raise NotImplementedError

    @abstractmethod
    def get_all(self, entity_type: str, entity_id: str) -> List[Any]:
        """Retrieve all stored values for a given entity, in insertion order."""
        raise NotImplementedError

    @abstractmethod
    def clear(self, entity_type: str, entity_id: str) -> None:
        """Remove all stored memory for a given entity."""
        raise NotImplementedError