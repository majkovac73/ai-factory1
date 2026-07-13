import asyncio
from app.core.providers.manager import ProviderManager
from app.services.log_service import LogService
from config import settings


class BaseAgent:
    """
    Shared base class for all LLM-backed agents (Planner, Generator,
    Executor, Critic, Fixer). Centralizes provider injection and model
    resolution so each agent doesn't duplicate the same init/call logic.
    """

    def __init__(self, provider=None, model: str = None, memory=None):
        self.llm = provider or ProviderManager.get_provider()
        self.model = model or settings.DEFAULT_MODEL
        self.memory = memory  # Optional[MemoryInterface]; concrete backend wired in step 26/27
        self.log_service = LogService()

    @staticmethod
    def _text_cost(model: str) -> float:
        """1-8: flat per-call cost estimate by model tier. Strong models
        (sonnet / non-mini gpt-4o / opus) cost more; everything else is the mini
        default."""
        m = (model or "").lower()
        strong = ("sonnet" in m) or ("opus" in m) or ("gpt-4o" in m and "mini" not in m) or ("claude-3" in m and "haiku" not in m)
        if strong:
            return float(getattr(settings, "TEXT_LLM_COST_USD_STRONG", 0.01))
        return float(getattr(settings, "TEXT_LLM_COST_USD", 0.002))

    def _generate(self, prompt: str, temperature: float = None) -> str:
        """
        Synchronous wrapper around the provider's async generate() call.
        Subclasses build the prompt, then call self._generate(prompt).
        Every call is logged (prompt + output + token usage) via LogService.
        Failures are logged too, before the exception propagates.

        1-6: `temperature` is threaded to the provider (judges pass 0.2 for
        low-variance scoring). 1-8: text-LLM spend is metered here (the choke
        point every text agent passes through) and the spend circuit breaker is
        checked BEFORE the paid call, so runaway text spend is bounded too —
        previously only image calls were metered/refused.
        """
        # 1-8: refuse past the daily ceiling (best-effort — a ledger error must
        # not take down every text call; only SpendCapExceeded propagates).
        try:
            from app.services.autonomy_service import AutonomyService, SpendCapExceeded
            try:
                AutonomyService().assert_within_circuit_breaker()
            except SpendCapExceeded:
                raise
            except Exception:
                pass
        except ImportError:
            pass

        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            output = asyncio.run(self.llm.generate(model=self.model, prompt=prompt, **kwargs))
        except Exception as e:
            self.log_service.error(
                source=self.__class__.__name__,
                message="LLM generation failed",
                payload={
                    "model": self.model,
                    "prompt": prompt,
                    "error": str(e),
                },
            )
            raise

        usage = getattr(self.llm, "last_usage", None)

        # 1-8: meter the text call in the daily spend ledger (best-effort).
        try:
            from app.services.autonomy_service import AutonomyService
            AutonomyService().record_spend(self._text_cost(self.model), f"text LLM ({self.__class__.__name__})")
        except Exception:
            pass

        self.log_service.info(
            source=self.__class__.__name__,
            message="LLM generation completed",
            payload={
                "model": self.model,
                "prompt": prompt,
                "output": output,
                "usage": usage,
            },
        )

        return output

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