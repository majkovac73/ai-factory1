"""
Shared in-process worker heartbeat registry.

Workers call record_heartbeat(name) at the top of each poll cycle.
The /health/workers endpoint reads from here.
"""
from datetime import datetime
from typing import Dict, Optional


_heartbeats: Dict[str, datetime] = {}
# P3-5: registry of live worker instances so the health check can RESTART a
# worker whose thread has died (not just alert about it).
_workers: Dict[str, object] = {}


def register_worker(worker_name: str, worker: object) -> None:
    _workers[worker_name] = worker


def get_worker(worker_name: str) -> Optional[object]:
    return _workers.get(worker_name)


def record_heartbeat(worker_name: str) -> None:
    _heartbeats[worker_name] = datetime.utcnow()


def get_heartbeats() -> Dict[str, Optional[datetime]]:
    return dict(_heartbeats)


def is_stale(worker_name: str, max_age_seconds: float) -> bool:
    last = _heartbeats.get(worker_name)
    if last is None:
        return True
    return (datetime.utcnow() - last).total_seconds() > max_age_seconds
