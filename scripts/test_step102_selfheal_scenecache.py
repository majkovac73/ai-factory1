"""
Step 102 / P3-5 + P3-6 test.

P3-5: EtsyReceiptWorker._check_worker_health restarts a registered worker whose
      thread has DIED (stale heartbeat + dead thread), and leaves a live one alone.
P3-6: MockupService._scene reuses cached scenes once enough exist (no new
      generation), and caches a freshly generated scene.

Usage: python scripts/test_step102_selfheal_scenecache.py
"""
import os
import sys
import tempfile

os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(tempfile.mkdtemp(), "images")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
from app.services import worker_registry
from app.workers.etsy_receipt_worker import EtsyReceiptWorker

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ---- P3-5: self-heal ----
class FakeWorker:
    def __init__(self, alive):
        self._thread = MagicMock()
        self._thread.is_alive.return_value = alive
        self.started = 0
    def start(self):
        self.started += 1
        self._thread.is_alive.return_value = True

dead = FakeWorker(alive=False)
live = FakeWorker(alive=True)
worker_registry.register_worker("AutonomyWorker", dead)
worker_registry.register_worker("MarketingRefreshWorker", live)

erw = EtsyReceiptWorker()
# Force both to look stale; keep EtsyReceiptWorker itself non-stale.
def fake_is_stale(name, max_age):
    return name in ("AutonomyWorker", "MarketingRefreshWorker")

with patch.object(worker_registry, "is_stale", side_effect=fake_is_stale), \
     patch("app.services.alert_service.AlertService") as _A:
    erw._check_worker_health()

check("P3-5 dead worker was restarted", dead.started == 1)
check("P3-5 live-but-stale worker NOT restarted (would duplicate)", live.started == 0)

# ---- P3-6: scene cache ----
from app.services.mockup_service import MockupService, _MAX_CACHED_SCENES
from app.core.paths import get_data_dir
from PIL import Image

gen_calls = {"n": 0}

class FakeProvider:
    async def generate_image(self, prompt, aspect_ratio=None, resolution=None):
        gen_calls["n"] += 1
        import base64, io, types
        buf = io.BytesIO(); Image.new("RGB", (256, 256), (200, 200, 200)).save(buf, "PNG")
        return types.SimpleNamespace(b64_data=base64.b64encode(buf.getvalue()).decode(), url=None)

svc = MockupService(image_provider=FakeProvider())

# First _MAX_CACHED_SCENES calls generate + cache; subsequent calls reuse.
for _ in range(_MAX_CACHED_SCENES):
    svc._scene("framed", 128)
gens_after_warmup = gen_calls["n"]
check("P3-6 warmup generated up to cache size", gens_after_warmup == _MAX_CACHED_SCENES)

scene_dir = get_data_dir() / "images" / "scenes"
cached = list(scene_dir.glob("framed_*.png"))
check("P3-6 scenes were cached to disk", len(cached) == _MAX_CACHED_SCENES)

# Now the cache is full — further calls must NOT generate.
for _ in range(4):
    svc._scene("framed", 128)
check("P3-6 full cache -> no further generation (reuse)", gen_calls["n"] == gens_after_warmup)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 self-heal + scene-cache tests passed.")
