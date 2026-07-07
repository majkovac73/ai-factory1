"""
Step 69 test: Product image generation agent.
Uses a test-double image provider — no real DALL-E API call.
"""
import sys
import asyncio
import uuid
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 69 — PRODUCT IMAGE AGENT TEST")
print("=" * 60)

from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult
from app.services.image_file_service import LISTING_DIR

# --- Test-double image provider (no API cost) ---
class FakeImageProvider(BaseImageProvider):
    def __init__(self):
        self.call_count = 0

    async def generate_image(self, prompt, size="1024x1024", model=None, **kw):
        self.call_count += 1
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        import base64, tempfile, os
        path = Path(tempfile.mktemp(suffix=".png"))
        path.write_bytes(fake_png)
        return ImageGenerationResult(
            b64_data=base64.b64encode(fake_png).decode(),
            provider="fake",
            model="fake-model",
        )

fake_provider = FakeImageProvider()

from app.agents.image.product_image_agent import ProductImageAgent

task_id = f"test-{uuid.uuid4().hex[:8]}"
agent = ProductImageAgent(image_provider=fake_provider)

print(f"\n[1] Generating listing images for task {task_id}...")
result = agent.generate_listing_images(
    task_id=task_id,
    product_name="Minimal Weekly Planner",
    visual_brief="Clean white background, pastel accents, serif typography, flat lay",
)

assert "hero" in result
assert "lifestyle" in result
hero_path: Path = result["hero"]
lifestyle_path: Path = result["lifestyle"]

assert hero_path.exists(), f"Hero image not found: {hero_path}"
assert lifestyle_path.exists(), f"Lifestyle image not found: {lifestyle_path}"
assert hero_path.name == "hero.png"
assert lifestyle_path.name == "lifestyle.png"
assert hero_path.parent == LISTING_DIR / task_id

print(f"  hero      : {hero_path}")
print(f"  lifestyle : {lifestyle_path}")

print("\n[2] Checking image provider was called twice (one per image)...")
assert fake_provider.call_count == 2, f"Expected 2 calls, got {fake_provider.call_count}"
print(f"  image_provider.generate_image called {fake_provider.call_count} times — correct")

print("\n[3] Testing run() entry point...")
agent2 = ProductImageAgent(image_provider=FakeImageProvider())
task_id2 = f"test-{uuid.uuid4().hex[:8]}"
result2 = agent2.run({"task_id": task_id2, "product_name": "Daily Habit Tracker", "visual_brief": "Bold colors"})
assert "hero" in result2

# Cleanup
shutil.rmtree(str(LISTING_DIR / task_id), ignore_errors=True)
shutil.rmtree(str(LISTING_DIR / task_id2), ignore_errors=True)
print("  run() returned correct keys; cleaned up test dirs")

print("\n" + "=" * 60)
print("STEP 69 TEST PASSED — product image agent works, test double used (no DALL-E API call)")
print("=" * 60)
