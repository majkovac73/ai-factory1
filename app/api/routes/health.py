from datetime import datetime, timezone

from fastapi import APIRouter

from app.services import worker_registry

router = APIRouter()

_WORKER_MAX_AGE = {
    "TaskWorker": 10,           # heartbeats every ~1s poll cycle
    "EtsyReceiptWorker": 660,   # polls every 300s — allow 2x + margin
    "AutonomyWorker": 7200,     # polls every 3600s — allow 2x
}


@router.get("")
@router.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "ai-factory",
    }


@router.get("/workers")
def worker_health():
    heartbeats = worker_registry.get_heartbeats()
    now = datetime.now(timezone.utc)
    workers = {}
    all_healthy = True

    for name, max_age in _WORKER_MAX_AGE.items():
        last = heartbeats.get(name)
        if last is None:
            age_seconds = None
            healthy = False
        else:
            # last is naive UTC — normalise for comparison
            if last.tzinfo is None:
                last_aware = last.replace(tzinfo=timezone.utc)
            else:
                last_aware = last
            age_seconds = round((now - last_aware).total_seconds(), 1)
            healthy = age_seconds <= max_age

        if not healthy:
            all_healthy = False

        workers[name] = {
            "healthy": healthy,
            "last_heartbeat": last.isoformat() if last else None,
            "age_seconds": age_seconds,
            "max_age_seconds": max_age,
        }

    return {
        "status": "ok" if all_healthy else "degraded",
        "workers": workers,
    }
