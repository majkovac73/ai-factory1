"""
Step 74 test: Pinterest image integration.
Uses test-double image provider — no real DALL-E API call or Pinterest API call.
"""
import sys
import uuid
import shutil
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 74 — PINTEREST IMAGE INTEGRATION TEST")
print("=" * 60)

from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult
from app.services.image_file_service import LISTING_DIR

class FakeImageProvider(BaseImageProvider):
    def __init__(self):
        self.call_count = 0
        self.last_size = None
    async def generate_image(self, prompt, size="1024x1024", model=None, **kw):
        self.call_count += 1
        self.last_size = size
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
        return ImageGenerationResult(
            b64_data=base64.b64encode(fake_png).decode(),
            provider="fake",
            model="fake-model",
        )

fake = FakeImageProvider()
task_id = f"test-{uuid.uuid4().hex[:8]}"

from app.services.pinterest_image_service import PinterestImageService

svc = PinterestImageService(image_provider=fake)

# 1. enrich_listing_with_image adds b64 keys
print(f"\n[1] Enriching listing with Pinterest image for task {task_id}...")
listing = {
    "product_name": "Minimalist Wall Calendar",
    "title": "Minimalist 2025 Wall Calendar — Printable",
    "description": "A clean, minimal wall calendar you can print at home.",
    "listing_url": "https://etsy.com/listing/test456",
}
enriched = svc.enrich_listing_with_image(listing, task_id, "Neutral tones, sans-serif, clean grid")

assert "image_base64" in enriched, "image_base64 key missing"
assert "image_content_type" in enriched
assert "pin_image_path" in enriched
assert enriched["image_content_type"] == "image/png"
decoded = base64.b64decode(enriched["image_base64"])
assert len(decoded) > 0
print(f"  image_base64 present ({len(decoded)} bytes)")
print(f"  pin_image_path: {enriched['pin_image_path']}")

# 2. Original listing keys are preserved
print("\n[2] Original listing keys preserved...")
assert enriched["title"] == listing["title"]
assert enriched["listing_url"] == listing["listing_url"]
print("  All original keys intact")

# 3. SocialImageAgent requested Pinterest size (1024x1792)
print("\n[3] Checking Pinterest portrait size was requested...")
from app.agents.image.social_image_agent import PINTEREST_SIZE
assert fake.last_size == PINTEREST_SIZE, f"Expected {PINTEREST_SIZE}, got {fake.last_size}"
print(f"  Requested size: {fake.last_size}")

# 4. PinterestChannel payload building includes image_base64 preference
print("\n[4] Verifying PinterestChannel prefers image_base64 over image_url...")
import importlib, app.marketing.pinterest_channel as pch_module
# Read updated source to verify conditional logic is present
import inspect
src = inspect.getsource(pch_module.PinterestChannel._post_async)
assert "image_base64" in src, "PinterestChannel does not handle image_base64"
assert "image_b64" in src, "PinterestChannel does not check image_b64"
# Preference: 'if image_b64' (b64 check) precedes 'elif image_url' (url check)
assert src.index("if image_b64") < src.index("elif image_url"), \
    "image_base64 should be preferred (if image_b64 before elif image_url)"
print("  PinterestChannel._post_async handles image_base64 (preferred over image_url)")

# Cleanup
shutil.rmtree(str(LISTING_DIR / task_id), ignore_errors=True)

print("\n" + "=" * 60)
print("STEP 74 TEST PASSED — Pinterest image integration works, test double used (no DALL-E or Pinterest API call)")
print("=" * 60)
