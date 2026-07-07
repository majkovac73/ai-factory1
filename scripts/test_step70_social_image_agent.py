"""
Step 70 test: Social media image generation agent (Pinterest).
Uses a test-double image provider — no real API call.
Updated for OpenRouter: verifies aspect_ratio='2:3' (native, not DALL-E approximation).
"""
import sys
import uuid
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 70 — SOCIAL IMAGE AGENT TEST (PINTEREST)")
print("=" * 60)

from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult
from app.services.image_file_service import LISTING_DIR
from app.agents.image.social_image_agent import SocialImageAgent, PINTEREST_ASPECT_RATIO, PINTEREST_RESOLUTION
import base64

class FakeImageProvider(BaseImageProvider):
    def __init__(self):
        self.last_aspect_ratio = None
        self.last_resolution = None
        self.last_prompt = None

    async def generate_image(self, prompt, size=None, model=None, aspect_ratio="1:1", resolution="1K", **kw):
        self.last_aspect_ratio = aspect_ratio
        self.last_resolution = resolution
        self.last_prompt = prompt
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        return ImageGenerationResult(
            b64_data=base64.b64encode(fake_png).decode(),
            provider="fake",
            model="fake-model",
        )

fake = FakeImageProvider()
task_id = f"test-{uuid.uuid4().hex[:8]}"
agent = SocialImageAgent(image_provider=fake)

print(f"\n[1] Generating Pinterest pin image for task {task_id}...")
result = agent.run({
    "task_id": task_id,
    "product_name": "Boho Wall Art Print",
    "visual_brief": "Earth tones, macrame textures, warm lighting",
    "listing_url": "https://etsy.com/listing/test123",
})
pin_path: Path = result["pin_image"]

assert pin_path.exists(), f"Pin image not saved: {pin_path}"
assert pin_path.name == "pin.png"
assert pin_path.parent == LISTING_DIR / task_id
print(f"  Saved: {pin_path}")

print("\n[2] Verifying native 2:3 aspect ratio and 4K resolution (Seedream min pixel requirement)...")
assert fake.last_aspect_ratio == PINTEREST_ASPECT_RATIO, f"Expected {PINTEREST_ASPECT_RATIO}, got {fake.last_aspect_ratio}"
assert PINTEREST_ASPECT_RATIO == "2:3", "PINTEREST_ASPECT_RATIO constant should be '2:3'"
assert fake.last_resolution == PINTEREST_RESOLUTION, f"Expected {PINTEREST_RESOLUTION}, got {fake.last_resolution}"
assert PINTEREST_RESOLUTION == "4K", "PINTEREST_RESOLUTION should be '4K' (2:3@2K is below Seedream's 3.69M px minimum)"
print(f"  aspect_ratio: {fake.last_aspect_ratio}  (native 2:3)")
print(f"  resolution  : {fake.last_resolution}  (4K required for 2:3 on Seedream 4.5)")

print("\n[3] Verifying prompt contains product name and Pinterest context...")
assert "Boho Wall Art Print" in fake.last_prompt
assert "portrait" in fake.last_prompt.lower() or "Pinterest" in fake.last_prompt
print("  Prompt contains product name and Pinterest framing")

# Cleanup
shutil.rmtree(str(LISTING_DIR / task_id), ignore_errors=True)

print("\n" + "=" * 60)
print("STEP 70 TEST PASSED — social image agent uses native 2:3 aspect ratio, test double used (no API call)")
print("=" * 60)
