from typing import Optional
from app.core.providers.openrouter_provider import OpenRouterProvider

class ProviderManager:
    _instance: Optional[OpenRouterProvider] = None

    @classmethod
    def get_provider(cls) -> OpenRouterProvider:
        """Singleton accessor to return the configured OpenRouter provider layer."""
        if cls._instance is None:
            cls._instance = OpenRouterProvider()
        return cls._instance