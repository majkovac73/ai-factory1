"""
OpenRouter image generation provider — replaces dalle3_provider.py.

Uses OpenRouter's dedicated Image API (POST https://openrouter.ai/api/v1/images),
the same OPENROUTER_API_KEY already funding the text pipeline.

Request shape (confirmed from OpenRouter docs):
  model, prompt, n, aspect_ratio, resolution, quality, output_format, seed, ...
Response shape:
  {"created": ..., "data": [{"b64_json": "<base64>"}], "usage": {...}}

Default model: settings.OPENROUTER_IMAGE_MODEL (currently
bytedance-seed/seedream-4.5). Supported aspect_ratios:
  1:1, 2:3, 3:2, 4:3, 3:4, 16:9, 9:16, 4:5, 5:4, 1:4, 4:1, 1:8, 8:1, 21:9
Supported resolutions: 512, 1K, 2K, 4K. Max n: 1.
"""
import os
import httpx

from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult
from app.core.providers.image_manager import ImageProviderManager
from config import settings

OPENROUTER_IMAGE_API = "https://openrouter.ai/api/v1/images"


class OpenRouterImageProvider(BaseImageProvider):
    """
    Concrete image generation provider backed by OpenRouter's Image API.
    Mirrors the auth/fail-loud pattern of OpenRouterProvider (text).
    Self-registers under "openrouter" on import.
    """

    def __init__(self):
        self._api_key = (
            getattr(settings, "OPENROUTER_API_KEY", None)
            or os.getenv("OPENROUTER_API_KEY")
        )
        if not self._api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set — required for OpenRouter image generation."
            )
        self._model = getattr(settings, "OPENROUTER_IMAGE_MODEL", "bytedance-seed/seedream-4.5")

    async def generate_image(
        self,
        prompt: str,
        size: str = None,
        model: str = None,
        aspect_ratio: str = "1:1",
        resolution: str = "1K",
        **kwargs,
    ) -> ImageGenerationResult:
        """
        Generate an image via the OpenRouter Image API.

        Args:
            prompt: Text description of the image to generate.
            size: Ignored (kept for BaseImageProvider compatibility); use
                  aspect_ratio + resolution instead for OpenRouter.
            model: Override model ID; defaults to settings.OPENROUTER_IMAGE_MODEL.
            aspect_ratio: One of the model's supported ratios (e.g. '1:1', '2:3').
            resolution: One of '512', '1K', '2K', '4K'.
            **kwargs: Any additional OpenRouter Image API parameters
                      (quality, output_format, seed, background, etc.).
        """
        model_name = model or self._model

        # 5-2: hard circuit breaker. can_spend() checks upstream are advisory and
        # can be raced by concurrent generations; this physically refuses to make
        # the paid call once the day's spend is past the ceiling, so a runaway
        # loop can't burn unbounded money before a human notices.
        # 5-3: let SpendCapExceeded propagate (that's the point), but a DIFFERENT
        # failure in the ledger (disk full, corrupt JSON, permissions) must NOT
        # take down every image generation — the wallet guard must not become a
        # single point of failure. Swallow those, loudly.
        from app.services.autonomy_service import AutonomyService, SpendCapExceeded
        try:
            AutonomyService().assert_within_circuit_breaker()
        except SpendCapExceeded:
            raise
        except Exception as e:
            import logging
            logging.getLogger("ai-factory").warning(
                f"OpenRouterImageProvider: spend circuit-breaker check failed soft "
                f"(proceeding): {e}"
            )

        payload = {
            "model": model_name,
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        for k, v in kwargs.items():
            if v is not None:
                payload[k] = v

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # Retry transient failures (5xx / 429 / network) with backoff. Image
        # generation has NO side effect, so re-issuing is safe (unlike a listing/
        # order POST) — a one-off Seedream 520 must not block an otherwise-good
        # product. Only a persistent error (or a 4xx that isn't 429) raises.
        import asyncio as _asyncio
        attempts = int(getattr(settings, "IMAGE_GEN_MAX_ATTEMPTS", 4))
        last_err = None
        data = None
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(OPENROUTER_IMAGE_API, json=payload, headers=headers)
                if response.status_code < 400:
                    data = response.json()
                    break
                # 4xx (except 429) is a real request problem — don't retry.
                if response.status_code != 429 and response.status_code < 500:
                    raise RuntimeError(f"OpenRouter Image API error {response.status_code}: {response.text}")
                last_err = f"{response.status_code}: {response.text[:200]}"
            except RuntimeError:
                raise
            except Exception as e:  # network/timeout — transient
                last_err = str(e)
            if attempt < attempts:
                delay = min(20.0, 1.5 * (2 ** (attempt - 1)))
                import logging as _lg
                _lg.getLogger("ai-factory").warning(
                    f"OpenRouterImageProvider: transient image error ({last_err}); "
                    f"retry {attempt + 1}/{attempts} in {delay:.0f}s"
                )
                await _asyncio.sleep(delay)
        if data is None:
            raise RuntimeError(f"OpenRouter Image API failed after {attempts} attempts: {last_err}")

        image_data = data["data"][0]
        usage = data.get("usage", {})

        # P0-13: record image spend at the single choke point every image passes
        # through, so the daily ledger is accurate (PDF pages, mockups, remakes,
        # pins — all counted). Best-effort: never let ledger I/O break generation.
        self._record_image_spend(model_name)

        return ImageGenerationResult(
            url=None,
            b64_data=image_data.get("b64_json"),
            provider="openrouter",
            model=model_name,
            raw_response=data,
        )


    @staticmethod
    def _record_image_spend(model_name: str = ""):
        try:
            from app.services.autonomy_service import AutonomyService
            cost = getattr(settings, "IMAGE_COST_USD", 0.04)
            AutonomyService().record_spend(cost, "image generation")
            # #4: per-task cost ledger (attributed via cost_context).
            from app.core.cost_context import record_cost
            record_cost(cost, use_case="image", provider="openrouter", model=model_name)
        except Exception:
            pass


ImageProviderManager.register_provider("openrouter", OpenRouterImageProvider)
