from typing import Any, List, Optional

from app.memory.short_term import ShortTermMemory
from app.memory.persistent import PersistentMemory


class MemoryRetriever:
    """
    Composition layer above the raw memory backends. Not itself a
    MemoryInterface implementation — it coordinates a ShortTermMemory
    cache in front of PersistentMemory (the durable source of truth),
    and provides agent-friendly helpers like get_context_string().

    Write-through: every add() writes to both short-term and persistent.
    Read: checks short-term first (fast); on a miss, falls back to
    persistent and backfills the short-term cache.
    """

    def __init__(self, short_term: ShortTermMemory = None, persistent: PersistentMemory = None):
        self.short_term = short_term or ShortTermMemory()
        self.persistent = persistent or PersistentMemory()

    def add(self, entity_type: str, entity_id: str, key: str, value: Any) -> None:
        self.short_term.add(entity_type, entity_id, key, value)
        self.persistent.add(entity_type, entity_id, key, value)

    def get(self, entity_type: str, entity_id: str, key: str) -> Optional[Any]:
        cached = self.short_term.get(entity_type, entity_id, key)
        if cached is not None:
            return cached

        value = self.persistent.get(entity_type, entity_id, key)
        if value is not None:
            self.short_term.add(entity_type, entity_id, key, value)
        return value

    def get_all(self, entity_type: str, entity_id: str) -> List[Any]:
        cached = self.short_term.get_all(entity_type, entity_id)
        if cached:
            return cached

        values = self.persistent.get_all(entity_type, entity_id)
        return values

    def get_context_string(self, entity_type: str, entity_id: str, separator: str = "\n") -> str:
        """
        Convenience helper for agents: returns all stored memory for an
        entity as a single newline-joined string, ready to be inserted
        into an LLM prompt as context. Non-string values are stringified.
        """
        values = self.get_all(entity_type, entity_id)
        return separator.join(str(v) for v in values)

    def clear(self, entity_type: str, entity_id: str) -> None:
        self.short_term.clear(entity_type, entity_id)
        self.persistent.clear(entity_type, entity_id)