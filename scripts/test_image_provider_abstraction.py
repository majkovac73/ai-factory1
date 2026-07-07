import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("IMAGE PROVIDER ABSTRACTION LAYER TEST")
print("=" * 60)

# 1. Confirm BaseImageProvider cannot be instantiated directly
print("\n[1] Checking BaseImageProvider is abstract...")
from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult

try:
    BaseImageProvider()
    print("✗ BaseImageProvider should not be directly instantiable")
    sys.exit(1)
except TypeError:
    print("✓ BaseImageProvider correctly cannot be instantiated directly")

# 2. Confirm ImageGenerationResult has expected fields
print("\n[2] Checking ImageGenerationResult structure...")
result = ImageGenerationResult(url="https://example.com/img.png", provider="test", model="test-model")
assert result.url == "https://example.com/img.png"
assert result.b64_data is None
assert result.provider == "test"
assert result.raw_response == {}
print("✓ ImageGenerationResult structure correct")

# 3. Confirm ImageProviderManager raises a clear error with nothing registered
print("\n[3] Checking ImageProviderManager fails loudly with no provider registered...")
from app.core.providers.image_manager import ImageProviderManager

ImageProviderManager.reset()
try:
    ImageProviderManager.get_provider()
    print("✗ Expected NotImplementedError since no provider is registered yet")
    sys.exit(1)
except NotImplementedError as e:
    print(f"✓ Correctly raised: {e}")

# 4. Confirm registering a dummy provider works end-to-end
print("\n[4] Checking provider registration + retrieval...")


class DummyImageProvider(BaseImageProvider):
    async def generate_image(self, prompt: str, size: str = "1024x1024", model=None, **kwargs) -> ImageGenerationResult:
        return ImageGenerationResult(url="https://dummy.test/image.png", provider="dummy", model=model or "dummy-model")


ImageProviderManager.register_provider("dummy_test_provider", DummyImageProvider)

import config
config.settings.IMAGE_PROVIDER = "dummy_test_provider"
ImageProviderManager.reset()

provider = ImageProviderManager.get_provider()
assert isinstance(provider, DummyImageProvider)
print("✓ Dummy provider correctly registered and retrieved")

# 5. Confirm the retrieved provider's generate_image() works via asyncio
print("\n[5] Checking generate_image() call through the abstraction...")
import asyncio

output = asyncio.run(provider.generate_image("a red bicycle"))
assert output.url == "https://dummy.test/image.png"
assert output.provider == "dummy"
print(f"✓ generate_image() returned: {output}")

print("\n" + "=" * 60)
print("IMAGE PROVIDER ABSTRACTION LAYER TEST COMPLETE")
print("=" * 60)