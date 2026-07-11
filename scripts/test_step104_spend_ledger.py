"""
Step 104-G test — 5-1 atomic/locked spend ledger, 5-2 spend circuit breaker.

Usage: python scripts/test_step104_spend_ledger.py
"""
import asyncio
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "g.db")
_data = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _data
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from app.services.autonomy_service import AutonomyService, SpendCapExceeded

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 5-1 concurrent record_spend under a shared lock loses nothing ──
import threading

svc = AutonomyService()
# force the ledger dir to our tmp regardless of get_data_dir wiring
svc._state_dir.mkdir(parents=True, exist_ok=True) if hasattr(svc._state_dir, "mkdir") else None

N = 50
def _spend():
    AutonomyService().record_spend(0.01, "concurrency test")

threads = [threading.Thread(target=_spend) for _ in range(N)]
for t in threads:
    t.start()
for t in threads:
    t.join()

total = round(AutonomyService()._load().get("spend_usd", 0.0), 4)
check(f"5-1 all {N} concurrent spends counted (expect 0.50, got {total})", abs(total - 0.50) < 1e-6)

# ── 5-1 write is atomic (no leftover .tmp files) ──
tmps = [p for p in svc._state_dir.glob("*.tmp*")]
check("5-1 no leftover temp ledger files", len(tmps) == 0)

# ── 5-2 circuit breaker raises past the ceiling ──
# ceiling = MAX_DAILY_SPEND_USD (5.0) * 1.5 = 7.5. Push spend over it.
AutonomyService().record_spend(8.0, "blow the ceiling")
raised = False
try:
    AutonomyService().assert_within_circuit_breaker()
except SpendCapExceeded:
    raised = True
check("5-2 assert_within_circuit_breaker raises past ceiling", raised)

# ── 5-2 provider refuses the paid call once over the ceiling ──
from app.core.providers.openrouter_image_provider import OpenRouterImageProvider
prov = OpenRouterImageProvider()
blocked = False
try:
    asyncio.run(prov.generate_image("test prompt"))
except SpendCapExceeded:
    blocked = True
except Exception:
    blocked = False
check("5-2 image provider raises SpendCapExceeded when over ceiling", blocked)

# ── under the ceiling, no raise ──
import shutil
fresh = tempfile.mkdtemp()
os.environ["DATA_DIR"] = fresh
# new instance reads the (empty) fresh ledger for a fresh day path? DATA_DIR is
# read at get_data_dir time; construct a fresh service pointing there.
svc2 = AutonomyService()
svc2._state_dir = __import__("pathlib").Path(fresh)
svc2._state_dir.mkdir(parents=True, exist_ok=True)
ok = True
try:
    svc2.assert_within_circuit_breaker()
except SpendCapExceeded:
    ok = False
check("5-2 no raise when ledger is empty/under ceiling", ok)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-G tests passed.")
