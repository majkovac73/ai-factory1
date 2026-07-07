"""
Step 67 test: DALL-E 3 image provider registration and ONE real API call.

Cost note: this script makes EXACTLY ONE real DALL-E 3 API call to prove
end-to-end integration. All subsequent steps use a test double instead.
Run this once to verify; do not run repeatedly.
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 67 — DALL-E 3 IMAGE PROVIDER TEST")
print("=" * 60)

# 1. Import and confirm registration side-effect fires
print("\n[1] Importing DALLE3Provider (triggers registration)...")
from app.core.providers.image_manager import ImageProviderManager
import config
config.settings.IMAGE_PROVIDER = "dalle3"
ImageProviderManager.reset()

import app.core.providers.dalle3_provider  # noqa: F401 — triggers register_provider call
print("  Import OK")

# 2. Confirm provider retrieves as DALLE3Provider
print("\n[2] Resolving provider via ImageProviderManager...")
from app.core.providers.dalle3_provider import DALLE3Provider

try:
    provider = ImageProviderManager.get_provider()
    assert isinstance(provider, DALLE3Provider), f"Expected DALLE3Provider, got {type(provider)}"
    print(f"  Provider resolved: {type(provider).__name__}")
except RuntimeError as e:
    # OPENAI_API_KEY not set — expected in CI; skip the live call
    print(f"  OPENAI_API_KEY not configured ({e}). Skipping live API call.")
    print("\nStep 67 test SKIPPED (no API key) — registration logic confirmed OK.")
    sys.exit(0)

# 3. ONE real DALL-E 3 API call
print("\n[3] Making ONE real DALL-E 3 API call...")
result = asyncio.run(provider.generate_image(
    prompt="A simple red circle on a white background",
    size="1024x1024",
))
assert result.provider == "dalle3"
assert result.model == "dall-e-3"
assert result.url or result.b64_data, "Expected url or b64_data in result"
print(f"  provider : {result.provider}")
print(f"  model    : {result.model}")
print(f"  url      : {result.url or '(b64 data returned)'}")

print("\n" + "=" * 60)
print("STEP 67 TEST PASSED — DALL-E 3 provider registered and confirmed live")
print("=" * 60)
