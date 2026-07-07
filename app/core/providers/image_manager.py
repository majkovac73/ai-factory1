from typing import Dict, Optional, Type

from app.core.providers.image_base import BaseImageProvider
from config import settings


class ImageProviderManager:
    """
    Singleton accessor for the configured image-generation provider,
    mirroring ProviderManager (app/core/providers/manager.py) for text
    providers.

    Providers register themselves via register_provider() rather than
    being hardcoded here, so Step 67 (DALL-E 3) only needs to add one
    new provider file plus a single registration call — this file
    doesn't need to change when a new provider is added.

    No provider is registered yet in this step. Calling get_provider()
    before Step 67 raises a clear NotImplementedError instead of
    silently returning None, so any code that tries to generate images
    early fails loudly with an actionable message.
    """

    _instance: Optional[BaseImageProvider] = None
    _registry: Dict[str, Type[BaseImageProvider]] = {}

    @classmethod
    def register_provider(cls, name: str, provider_class: Type[BaseImageProvider]):
        cls._registry[name] = provider_class

    @classmethod
    def get_provider(cls) -> BaseImageProvider:
        if cls._instance is not None:
            return cls._instance

        provider_name = settings.IMAGE_PROVIDER
        provider_class = cls._registry.get(provider_name)

        if provider_class is None:
            raise NotImplementedError(
                f"No image provider registered for '{provider_name}'. "
                f"Registered providers: {list(cls._registry.keys()) or 'none'}. "
                f"DALL-E 3 support is added in Step 67 — until then, no image "
                f"generation provider is available."
            )

        cls._instance = provider_class()
        return cls._instance

    @classmethod
    def reset(cls):
        """Clears the cached singleton instance. Used by tests so a
        freshly-registered provider is picked up instead of a stale one."""
        cls._instance = None