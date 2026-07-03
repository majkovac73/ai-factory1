import asyncio
from app.core.providers.manager import ProviderManager
from config import settings


class BaseAgent:
    """
    Shared base class for all LLM-backed agents (Planner, Generator,
    Executor, Critic, Fixer). Centralizes provider injection and model
    resolution so each agent doesn't duplicate the same init/call logic.
    """

    def __init__(self, provider=None, model: str = None):
        self.llm = provider or ProviderManager.get_provider()
        self.model = model or settings.DEFAULT_MODEL

    def _generate(self, prompt: str) -> str:
        """
        Synchronous wrapper around the provider's async generate() call.
        Subclasses build the prompt, then call self._generate(prompt).
        """
        return asyncio.run(self.llm.generate(model=self.model, prompt=prompt))

    def run(self, *args, **kwargs):
        """
        Standardized entry point so calling code (e.g. Orchestrator) can
        invoke any agent the same way, without knowing its specific
        method name. Subclasses must override this to call their own
        specific method (create_plan, execute_step, review, etc).
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement run()"
        )