"""
OpenRouter image generation provider — replaces dalle3_provider.py.

Uses OpenRouter's dedicated Image API (POST https://openrouter.ai/api/v1/images),
the same OPENROUTER_API_KEY already funding the text pipeline.

Request shape (confirmed from OpenRouter docs):
  model, prompt, n, aspect_ratio, resolution, quality, output_format, seed, ...
Response shape:
  {"created": ..., "data": [{"b64_json": "<base64>"}], "usage": {...}}

Default model: google/gemini-3.1-flash-image (configurable via
settings.OPENROUTER_IMAGE_MODEL). Supported aspect_ratios for this model:
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
        self._model = getattr(settings, "OPENROUTER_IMAGE_MODEL", "google/gemini-3.1-flash-image")

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

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                OPENROUTER_IMAGE_API, json=payload, headers=headers
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"OpenRouter Image API error {response.status_code}: {response.text}"
                )
            data = response.json()

        image_data = data["data"][0]
        usage = data.get("usage", {})

        # P0-13: record image spend at the single choke point every image passes
        # through, so the daily ledger is accurate (PDF pages, mockups, remakes,
        # pins — all counted). Best-effort: never let ledger I/O break generation.
        self._record_image_spend()

        return ImageGenerationResult(
            url=None,
            b64_data=image_data.get("b64_json"),
            provider="openrouter",
            model=model_name,
            raw_response=data,
        )


    @staticmethod
    def _record_image_spend():
        try:
            from app.services.autonomy_service import AutonomyService
            cost = getattr(settings, "IMAGE_COST_USD", 0.04)
            AutonomyService().record_spend(cost, "image generation")
        except Exception:
            pass


ImageProviderManager.register_provider("openrouter", OpenRouterImageProvider)
