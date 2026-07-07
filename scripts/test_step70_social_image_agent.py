"""
Step 70 test: Social media image generation agent (Pinterest).
Uses a test-double image provider — no real DALL-E API call.
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
from app.agents.image.social_image_agent import SocialImageAgent, PINTEREST_SIZE
import base64

class FakeImageProvider(BaseImageProvider):
    def __init__(self):
        self.last_size = None
        self.last_prompt = None

    async def generate_image(self, prompt, size="1024x1024", model=None, **kw):
        self.last_size = size
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

print("\n[2] Verifying Pinterest-sized image request (1024x1792)...")
assert fake.last_size == PINTEREST_SIZE, f"Expected {PINTEREST_SIZE}, got {fake.last_size}"
print(f"  Requested size: {fake.last_size}")

print("\n[3] Verifying prompt contains product name and Pinterest context...")
assert "Boho Wall Art Print" in fake.last_prompt
assert "portrait" in fake.last_prompt.lower() or "Pinterest" in fake.last_prompt
print("  Prompt contains product name and Pinterest framing")

# Cleanup
shutil.rmtree(str(LISTING_DIR / task_id), ignore_errors=True)

print("\n" + "=" * 60)
print("STEP 70 TEST PASSED — social image agent works, test double used (no DALL-E API call)")
print("=" * 60)
