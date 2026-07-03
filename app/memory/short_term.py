from typing import Any, Dict, List, Optional, Tuple

from app.memory.base import MemoryInterface


class ShortTermMemory(MemoryInterface):
    """
    In-process, in-memory implementation of MemoryInterface. Data lives
    only as long as this object exists (e.g. for the duration of a
    single task run or process lifetime) and is lost on restart.

    Storage is keyed by (entity_type, entity_id) -> ordered dict of
    {memory_key: memory_value}, so insertion order is preserved for
    get_all().
    """

    def __init__(self):
        self._store: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add(self, entity_type: str, entity_id: str, key: str, value: Any) -> None:
        scope = (entity_type, entity_id)
        if scope not in self._store:
            self._store[scope] = {}
        self._store[scope][key] = value

    def get(self, entity_type: str, entity_id: str, key: str) -> Optional[Any]:
        scope = (entity_type, entity_id)
        return self._store.get(scope, {}).get(key)

    def get_all(self, entity_type: str, entity_id: str) -> List[Any]:
        scope = (entity_type, entity_id)
        return list(self._store.get(scope, {}).values())

    def clear(self, entity_type: str, entity_id: str) -> None:
        scope = (entity_type, entity_id)
        self._store.pop(scope, None)