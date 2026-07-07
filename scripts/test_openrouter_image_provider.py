"""
OpenRouter image provider test — replaces test_step67_dalle3_provider.py.

Makes ONE real OpenRouter Image API call to confirm end-to-end integration
and print the actual returned image dimensions (used to ground-truth the
aspect-ratio corrections in step 3).
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
result = asyncio.run(provider.generate_image(
    prompt="A simple solid red circle centered on a plain white background",
    aspect_ratio="1:1",
    resolution="1K",
))
assert result.provider == "openrouter"
assert result.model == "google/gemini-3.1-flash-image"
assert result.b64_data, "Expected b64_data in result"
print(f"  provider : {result.provider}")
print(f"  model    : {result.model}")
print(f"  b64_data : {len(result.b64_data)} chars")

# 4. Decode and measure actual pixel dimensions
print("\n[4] Decoding image to measure actual dimensions...")
try:
    from PIL import Image as PILImage
    img_bytes = base64.b64decode(result.b64_data)
    img = PILImage.open(BytesIO(img_bytes))
    width, height = img.size
    print(f"  Actual dimensions : {width} x {height} px")
    print(f"  Actual ratio      : {width}:{height} = {width/height:.4f}")
    print(f"  Format            : {img.format}")
except ImportError:
    print("  Pillow not available — skipping dimension decode")
    width, height = None, None

# 5. Confirm raw_response structure
print("\n[5] Checking raw_response structure...")
assert "data" in result.raw_response
assert "b64_json" in result.raw_response["data"][0]
usage = result.raw_response.get("usage", {})
print(f"  usage: {usage}")

print("\n" + "=" * 60)
print(f"OPENROUTER IMAGE PROVIDER TEST PASSED")
if width and height:
    print(f"CONFIRMED OUTPUT DIMENSIONS: {width}x{height} px (1:1 @ 1K)")
print("Real OpenRouter API call made — one image generated.")
print("=" * 60)
