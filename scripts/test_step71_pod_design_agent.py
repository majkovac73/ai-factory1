"""
Step 71 test: POD design generation agent (double duty: digital download + POD).
Uses a test-double image provider — no real DALL-E API call.
"""
import sys
import uuid
import shutil
from pathlib import Path
import base64

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 71 — POD DESIGN AGENT TEST")
print("=" * 60)

from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult
from app.services.image_file_service import DELIVERY_DIR
from app.agents.image.pod_design_agent import PODDesignAgent

class FakeImageProvider(BaseImageProvider):
    def __init__(self):
        self.last_prompt = None
    async def generate_image(self, prompt, size="1024x1024", model=None, **kw):
        self.last_prompt = prompt
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        return ImageGenerationResult(
            b64_data=base64.b64encode(fake_png).decode(),
            provider="fake",
            model="fake-model",
        )

# Test 1: digital download variant
print("\n[1] Generating digital download design...")
fake = FakeImageProvider()
task_id = f"test-{uuid.uuid4().hex[:8]}"
agent = PODDesignAgent(image_provider=fake)
result = agent.run({
    "task_id": task_id,
    "product_name": "Monthly Budget Planner Page",
    "visual_brief": "Soft sage green, minimal grid layout, sans-serif font hints",
    "product_type": "digital_download",
})
assert "design_path" in result
assert "product_type" in result
design_path: Path = result["design_path"]
assert design_path.exists()
assert design_path.parent == DELIVERY_DIR / task_id, f"Should be in delivery dir, got {design_path.parent}"
assert "printable digital download" in fake.last_prompt
print(f"  Saved (delivery): {design_path}")
print(f"  product_type    : {result['product_type']}")

# Test 2: POD variant
print("\n[2] Generating POD design...")
fake2 = FakeImageProvider()
task_id2 = f"test-{uuid.uuid4().hex[:8]}"
agent2 = PODDesignAgent(image_provider=fake2)
result2 = agent2.run({
    "task_id": task_id2,
    "product_name": "Floral Mug Wrap",
    "visual_brief": "Watercolor botanicals, blush and cream palette",
    "product_type": "pod",
})
design_path2: Path = result2["design_path"]
assert design_path2.parent == DELIVERY_DIR / task_id2
assert "print-on-demand" in fake2.last_prompt
print(f"  Saved (delivery): {design_path2}")

# Test 3: file is readable / non-empty
print("\n[3] Verifying saved file is non-empty...")
assert design_path.stat().st_size > 0
print(f"  File size: {design_path.stat().st_size} bytes")

# Cleanup
shutil.rmtree(str(DELIVERY_DIR / task_id), ignore_errors=True)
shutil.rmtree(str(DELIVERY_DIR / task_id2), ignore_errors=True)

print("\n" + "=" * 60)
print("STEP 71 TEST PASSED — POD design agent works (digital_download + pod), test double used (no DALL-E API call)")
print("=" * 60)
