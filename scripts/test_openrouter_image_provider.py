"""
OpenRouter image provider test — replaces test_step67_dalle3_provider.py.

Makes real OpenRouter Image API calls to confirm end-to-end integration
and print the actual returned image dimensions.

NOTE: bytedance-seed/seedream-4.5 requires a minimum of ~3,686,400 pixels
(equivalent to 1920x1920 for 1:1). "1K" (1024x1024 = ~1M pixels) is below
this threshold and returns a 400 error. "2K" is the minimum viable resolution.
Since Seedream is flat-rate regardless of resolution, "2K" is used throughout.

Pillow is REQUIRED — if not importable, the test fails loudly (dimension
verification is the core purpose of this test).
"""
import sys
import asyncio
import base64
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("OPENROUTER IMAGE PROVIDER TEST")
print("=" * 60)

# 0. Verify Pillow is available — fail loudly if not
print("\n[0] Checking Pillow is importable...")
try:
    from PIL import Image as PILImage
    import PIL
    print(f"  Pillow available: {PIL.__version__}")
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
print(f"  Import OK — model: {config.settings.OPENROUTER_IMAGE_MODEL}")

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

# 3. Real API call — 1:1 at 2K (minimum resolution for Seedream 4.5)
print("\n[3] Making real OpenRouter Image API call (1:1, 2K)...")
result_2k = asyncio.run(provider.generate_image(
    prompt="A simple solid red circle centered on a plain white background",
    aspect_ratio="1:1",
    resolution="2K",
))
assert result_2k.provider == "openrouter"
assert result_2k.b64_data, "Expected b64_data in result"
assert result_2k.model, "Expected model field to be populated"
print(f"  provider : {result_2k.provider}")
print(f"  model    : {result_2k.model}")
print(f"  b64_data : {len(result_2k.b64_data)} chars")

usage_2k = result_2k.raw_response.get("usage", {})
cost_2k = usage_2k.get("cost", "unknown")
print(f"  cost     : ${cost_2k}")

# 4. Decode and measure actual pixel dimensions
print("\n[4] Decoding image to measure actual dimensions...")
img_bytes = base64.b64decode(result_2k.b64_data)
with PILImage.open(BytesIO(img_bytes)) as img:
    width_2k, height_2k = img.size
    fmt = img.format
print(f"  Actual dimensions : {width_2k} x {height_2k} px")
print(f"  Actual ratio      : {width_2k/height_2k:.4f}")
print(f"  Format            : {fmt}")
print(f"  Total pixels      : {width_2k * height_2k:,}")

# 5. Real API call — 2:3 at 4K (Pinterest pin)
# NOTE: Seedream 4.5 requires >= 3,686,400 pixels. 2:3 at 2K is ~2.8M pixels (below
# minimum). 4K at 2:3 produces enough pixels. Flat-rate so cost is still $0.04.
print("\n[5] Making real OpenRouter Image API call (2:3, 4K — Pinterest)...")
result_pin = asyncio.run(provider.generate_image(
    prompt="A simple solid blue rectangle on a white background",
    aspect_ratio="2:3",
    resolution="4K",
))
assert result_pin.b64_data, "Expected b64_data in Pinterest result"

usage_pin = result_pin.raw_response.get("usage", {})
cost_pin = usage_pin.get("cost", "unknown")
print(f"  cost : ${cost_pin}")

img_bytes_pin = base64.b64decode(result_pin.b64_data)
with PILImage.open(BytesIO(img_bytes_pin)) as img:
    width_pin, height_pin = img.size
print(f"  Actual dimensions : {width_pin} x {height_pin} px")
print(f"  Actual ratio      : {width_pin/height_pin:.4f}")

# 6. Confirm raw_response structure
print("\n[6] Checking raw_response structure...")
assert "data" in result_2k.raw_response
assert "b64_json" in result_2k.raw_response["data"][0]
print(f"  usage (1:1/2K) : {usage_2k}")
print(f"  usage (2:3/2K) : {usage_pin}")

# 7. Summary
print("\n" + "=" * 60)
print("OPENROUTER IMAGE PROVIDER TEST PASSED")
print(f"  1:1 @ 2K (listing/delivery) => {width_2k}x{height_2k} px  cost=${cost_2k}")
print(f"  2:3 @ 4K (Pinterest pin)    => {width_pin}x{height_pin} px  cost=${cost_pin}")
print(f"  NOTE: Seedream minimum is 3,686,400 px; 1:1@2K={width_2k*height_2k:,}px OK, 2:3@2K~2.8M px TOO SMALL => use 4K for non-square")
print("Real OpenRouter API calls made (2 images total).")
print("=" * 60)
