"""
OpenRouter image provider test — replaces test_step67_dalle3_provider.py.

Makes ONE real OpenRouter Image API call to confirm end-to-end integration
and print the actual returned image dimensions (used to ground-truth the
aspect-ratio and minimum-resolution constants in image_validation_service.py).

Pillow is REQUIRED — if it is not importable, the test fails loudly rather
than silently skipping the dimension check (the core purpose of this test).
"""
import sys
import asyncio
import base64
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("OPENROUTER IMAGE PROVIDER TEST (replaces step67 DALL-E test)")
print("=" * 60)

# 0. Verify Pillow is available — fail loudly if not
print("\n[0] Checking Pillow is importable...")
try:
    from PIL import Image as PILImage
    print(f"  Pillow available: {PILImage.__version__ if hasattr(PILImage, '__version__') else 'OK'}")
except ImportError as e:
    print(f"  ERROR: Pillow is not importable: {e}")
    print("  Dimension verification is the core purpose of this test — cannot pass without it.")
    print("  Fix: pip install Pillow  (and add Pillow to requirements.txt)")
    sys.exit(1)

# 1. Confirm registration fires on import
print("\n[1] Importing OpenRouterImageProvider (triggers registration)...")
import config
config.settings.IMAGE_PROVIDER = "openrouter"

from app.core.providers.image_manager import ImageProviderManager
ImageProviderManager.reset()

import app.core.providers.openrouter_image_provider  # noqa: F401
print("  Import OK")

# 2. Resolve provider
print("\n[2] Resolving provider via ImageProviderManager...")
from app.core.providers.openrouter_image_provider import OpenRouterImageProvider

try:
    provider = ImageProviderManager.get_provider()
    assert isinstance(provider, OpenRouterImageProvider)
    print(f"  Provider resolved: {type(provider).__name__}")
except RuntimeError as e:
    print(f"  OPENROUTER_API_KEY not configured: {e}")
    sys.exit(1)

# 3. ONE real API call — square 1:1 at 1K resolution
print("\n[3] Making ONE real OpenRouter Image API call (1:1, 1K)...")
result_1k = asyncio.run(provider.generate_image(
    prompt="A simple solid red circle centered on a plain white background",
    aspect_ratio="1:1",
    resolution="1K",
))
assert result_1k.provider == "openrouter"
assert result_1k.model == "google/gemini-3.1-flash-image"
assert result_1k.b64_data, "Expected b64_data in result"
print(f"  provider : {result_1k.provider}")
print(f"  model    : {result_1k.model}")
print(f"  b64_data : {len(result_1k.b64_data)} chars")

usage_1k = result_1k.raw_response.get("usage", {})
cost_1k = usage_1k.get("cost", "unknown")
print(f"  cost     : ${cost_1k}")

# 4. Decode and measure actual pixel dimensions (1:1 / 1K)
print("\n[4] Decoding 1:1/1K image to measure actual dimensions...")
img_bytes_1k = base64.b64decode(result_1k.b64_data)
with PILImage.open(BytesIO(img_bytes_1k)) as img:
    width_1k, height_1k = img.size
    fmt_1k = img.format
print(f"  Actual dimensions : {width_1k} x {height_1k} px")
print(f"  Actual ratio      : {width_1k/height_1k:.4f}")
print(f"  Format            : {fmt_1k}")

# 5. ONE real API call — square 1:1 at 2K resolution (delivery quality cost check)
print("\n[5] Making ONE real OpenRouter Image API call (1:1, 2K) for delivery cost data...")
result_2k = asyncio.run(provider.generate_image(
    prompt="A simple solid red circle centered on a plain white background",
    aspect_ratio="1:1",
    resolution="2K",
))
assert result_2k.b64_data, "Expected b64_data in 2K result"

usage_2k = result_2k.raw_response.get("usage", {})
cost_2k = usage_2k.get("cost", "unknown")
print(f"  cost : ${cost_2k}")

img_bytes_2k = base64.b64decode(result_2k.b64_data)
with PILImage.open(BytesIO(img_bytes_2k)) as img:
    width_2k, height_2k = img.size
print(f"  Actual dimensions : {width_2k} x {height_2k} px")

# 6. Confirm raw_response structure
print("\n[6] Checking raw_response structure...")
assert "data" in result_1k.raw_response
assert "b64_json" in result_1k.raw_response["data"][0]
print(f"  1K usage: {usage_1k}")
print(f"  2K usage: {usage_2k}")

# 7. Summary
print("\n" + "=" * 60)
print("OPENROUTER IMAGE PROVIDER TEST PASSED")
print(f"  1:1 @ 1K => {width_1k}x{height_1k} px  cost=${cost_1k}")
print(f"  1:1 @ 2K => {width_2k}x{height_2k} px  cost=${cost_2k}")
print("Real OpenRouter API calls made (2 images total).")
print("=" * 60)
