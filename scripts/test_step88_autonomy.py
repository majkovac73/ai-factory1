"""
Step 88 test — AutonomyService + AutonomyWorker

Tests:
  [1] AutonomyService: fresh state shows zero tasks and zero spend
  [2] can_create_task() returns True when under cap
  [3] record_task_created() increments counter
  [4] can_create_task() returns False after exceeding cap
  [5] can_spend() and record_spend() work correctly
  [6] spend cap blocks further calls once limit reached
  [7] AutonomyWorker with AUTONOMY_ENABLED=False does not call TrendResearchAgent
  [8] AutonomyWorker with AUTONOMY_ENABLED=True calls agent double and creates task

Usage:
  python scripts/test_step88_autonomy.py
"""
import sys
import os
import tempfile
import types

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

# ── Env-level isolation for get_data_dir() ────────────────────────────────────
# AutonomyService.__init__ calls get_data_dir() to resolve _state_dir.
# On Railway, IMAGE_STORAGE_ROOT=/data/images and DATABASE_PATH=/data/app.db
# both point get_data_dir() at /data — meaning any unguarded AutonomyService()
# instantiation would write autonomy_state_<date>.json to the production volume.
#
# Fix: redirect IMAGE_STORAGE_ROOT to a per-run temp directory before any app
# imports, so get_data_dir() resolves to the temp dir for the entire test
# process. Clear DATABASE_PATH for the same reason (it's the fallback path).
# Each individual test also overrides _state_dir directly via __new__ — that's
# belt-and-suspenders; this env-level redirect is the primary guard.
_state_tmp = tempfile.mkdtemp(suffix=".autonomy_test")
os.environ.pop("DATABASE_PATH", None)
os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(_state_tmp, "images")

import logging
logging.basicConfig(level=logging.WARNING)

from dotenv import load_dotenv
load_dotenv()

_passed = 0
_failed = 0


def ok(label: str):
    global _passed
    _passed += 1
    print(f"  [PASS] {label}")


def fail(label: str, reason: str):
    global _failed
    _failed += 1
    print(f"  [FAIL] {label}: {reason}")


# ── Patch AutonomyService to use a temp dir ────────────────────────────────────

def make_autonomy_service(max_tasks=3, max_spend=1.00, tmp_dir=None):
    from app.services.autonomy_service import AutonomyService
    from unittest.mock import patch
    from pathlib import Path
    import config.settings as _settings_mod

    svc = AutonomyService.__new__(AutonomyService)
    svc._state_dir = Path(tmp_dir)
    svc._state_dir.mkdir(parents=True, exist_ok=True)
    return svc


# ── Tests ──────────────────────────────────────────────────────────────────────

print("\nStep 88 — AutonomyService + AutonomyWorker tests\n")

# Save original settings to restore after patches
from config import settings as cfg

# [1] Fresh state
with tempfile.TemporaryDirectory() as tmp:
    from app.services.autonomy_service import AutonomyService
    from pathlib import Path

    svc = AutonomyService.__new__(AutonomyService)
    svc._state_dir = Path(tmp)

    status = svc.daily_status()
    if status["tasks_created"] == 0 and status["spend_usd"] == 0.0:
        ok("[1] fresh daily state is zero tasks, zero spend")
    else:
        fail("[1] fresh daily state", f"got {status}")

# [2] can_create_task under cap
with tempfile.TemporaryDirectory() as tmp:
    svc = AutonomyService.__new__(AutonomyService)
    svc._state_dir = Path(tmp)

    orig = cfg.MAX_TASKS_PER_DAY
    cfg.MAX_TASKS_PER_DAY = 3
    try:
        result = svc.can_create_task()
    finally:
        cfg.MAX_TASKS_PER_DAY = orig

    if result is True:
        ok("[2] can_create_task() True when under cap")
    else:
        fail("[2] can_create_task()", "returned False unexpectedly")

# [3] record_task_created increments counter
with tempfile.TemporaryDirectory() as tmp:
    svc = AutonomyService.__new__(AutonomyService)
    svc._state_dir = Path(tmp)

    orig = cfg.MAX_TASKS_PER_DAY
    cfg.MAX_TASKS_PER_DAY = 99  # avoid accidental cap alert
    try:
        svc.record_task_created()
        svc.record_task_created()
        status = svc.daily_status()
    finally:
        cfg.MAX_TASKS_PER_DAY = orig

    if status["tasks_created"] == 2:
        ok("[3] record_task_created() increments counter correctly")
    else:
        fail("[3] record_task_created()", f"expected 2, got {status['tasks_created']}")

# [4] can_create_task False after cap
with tempfile.TemporaryDirectory() as tmp:
    svc = AutonomyService.__new__(AutonomyService)
    svc._state_dir = Path(tmp)

    orig = cfg.MAX_TASKS_PER_DAY
    cfg.MAX_TASKS_PER_DAY = 2
    try:
        svc.record_task_created()
        svc.record_task_created()
        result = svc.can_create_task()
    finally:
        cfg.MAX_TASKS_PER_DAY = orig

    if result is False:
        ok("[4] can_create_task() False after cap reached")
    else:
        fail("[4] can_create_task() after cap", "returned True unexpectedly")

# [5] can_spend and record_spend
with tempfile.TemporaryDirectory() as tmp:
    svc = AutonomyService.__new__(AutonomyService)
    svc._state_dir = Path(tmp)

    orig = cfg.MAX_DAILY_SPEND_USD
    cfg.MAX_DAILY_SPEND_USD = 1.00
    try:
        assert svc.can_spend(0.50) is True, "should allow $0.50"
        svc.record_spend(0.50, "test")
        assert svc.can_spend(0.50) is True, "should allow another $0.50 (total $1.00)"
        svc.record_spend(0.50, "test")
        ok("[5] can_spend() and record_spend() accumulate correctly")
    except AssertionError as e:
        fail("[5] can_spend/record_spend", str(e))
    finally:
        cfg.MAX_DAILY_SPEND_USD = orig

# [6] spend cap blocks
with tempfile.TemporaryDirectory() as tmp:
    svc = AutonomyService.__new__(AutonomyService)
    svc._state_dir = Path(tmp)

    orig = cfg.MAX_DAILY_SPEND_USD
    cfg.MAX_DAILY_SPEND_USD = 1.00
    try:
        svc.record_spend(0.90, "test")
        result = svc.can_spend(0.20)
    finally:
        cfg.MAX_DAILY_SPEND_USD = orig

    if result is False:
        ok("[6] can_spend() False when cap would be exceeded")
    else:
        fail("[6] can_spend() cap block", "returned True unexpectedly")

# [7] AutonomyWorker._run_loop with AUTONOMY_ENABLED=False does NOT call _run_cycle
import threading as _threading
import unittest.mock as _mock

cycle_called = []

orig_enabled = cfg.AUTONOMY_ENABLED
cfg.AUTONOMY_ENABLED = False

try:
    from app.workers.autonomy_worker import AutonomyWorker

    worker7 = AutonomyWorker.__new__(AutonomyWorker)
    worker7._schedule_seconds = 0  # don't wait between iterations
    worker7._stop_event = _threading.Event()

    with _mock.patch.object(worker7, "_run_cycle", side_effect=lambda: cycle_called.append(True)):
        # Let it run one iteration then stop
        def _stopper():
            import time; time.sleep(0.1)
            worker7._stop_event.set()
        t = _threading.Thread(target=_stopper)
        t.start()
        worker7._run_loop()
        t.join()
finally:
    cfg.AUTONOMY_ENABLED = orig_enabled

if not cycle_called:
    ok("[7] AUTONOMY_ENABLED=False: _run_loop skips _run_cycle")
else:
    fail("[7] AUTONOMY_ENABLED=False", "_run_cycle was called unexpectedly")

# [8] AutonomyWorker with agent double — creates task
with tempfile.TemporaryDirectory() as tmp:

    created_tasks = []

    class FakeAgent2:
        def run(self):
            return {"concept": "Celestial moon phase art print"}

    class FakeTaskService:
        def create_task(self, task_create):
            class FakeTask:
                id = "fake-task-id-88"
            created_tasks.append(task_create.prompt)
            return FakeTask()

    from app.services.autonomy_service import AutonomyService
    from pathlib import Path
    import app.agents.trend_research_agent as _tra_mod
    import app.workers.autonomy_worker as _aw_mod

    svc_double = AutonomyService.__new__(AutonomyService)
    svc_double._state_dir = Path(tmp)

    orig_cls2 = _tra_mod.TrendResearchAgent

    orig_enabled2 = cfg.AUTONOMY_ENABLED
    orig_tasks = cfg.MAX_TASKS_PER_DAY
    orig_spend = cfg.MAX_DAILY_SPEND_USD

    cfg.AUTONOMY_ENABLED = True
    cfg.MAX_TASKS_PER_DAY = 10
    cfg.MAX_DAILY_SPEND_USD = 5.00

    try:
        _tra_mod.TrendResearchAgent = FakeAgent2

        worker2 = AutonomyWorker.__new__(AutonomyWorker)
        worker2._schedule_seconds = 60
        worker2._stop_event = __import__("threading").Event()

        # Patch imports used inside _run_cycle
        import unittest.mock as _mock
        with _mock.patch("app.workers.autonomy_worker.settings", cfg):
            with _mock.patch("app.services.autonomy_service.settings", cfg):
                # Inject our doubles
                import app.services.autonomy_service as _as_mod
                orig_svc_cls = _as_mod.AutonomyService

                class PatchedAutonomyService(AutonomyService):
                    def __init__(self):
                        self._state_dir = Path(tmp)
                        self._state_dir.mkdir(parents=True, exist_ok=True)

                _as_mod.AutonomyService = PatchedAutonomyService

                import app.services.task_service as _ts_mod
                orig_ts_cls = _ts_mod.TaskService
                _ts_mod.TaskService = FakeTaskService

                try:
                    worker2._run_cycle()
                finally:
                    _as_mod.AutonomyService = orig_svc_cls
                    _ts_mod.TaskService = orig_ts_cls
    except Exception as e:
        fail("[8] AutonomyWorker creates task via double", str(e))
        created_tasks.clear()
    finally:
        _tra_mod.TrendResearchAgent = orig_cls2
        cfg.AUTONOMY_ENABLED = orig_enabled2
        cfg.MAX_TASKS_PER_DAY = orig_tasks
        cfg.MAX_DAILY_SPEND_USD = orig_spend

    if created_tasks and created_tasks[0] == "Celestial moon phase art print":
        ok("[8] AUTONOMY_ENABLED=True: agent double called, task created with correct concept")
    elif not created_tasks:
        fail("[8] AutonomyWorker task creation", "no task was created")
    else:
        fail("[8] AutonomyWorker task creation", f"wrong concept: {created_tasks}")

# ── Summary ────────────────────────────────────────────────────────────────────

print(f"\nResults: {_passed} passed, {_failed} failed\n")

# Clean up the process-level temp dir used for env-level isolation
import shutil as _shutil
try:
    _shutil.rmtree(_state_tmp, ignore_errors=True)
except Exception:
    pass

sys.exit(0 if _failed == 0 else 1)
