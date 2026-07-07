import base64
import httpx

from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult
from app.core.providers.image_manager import ImageProviderManager
from config import settings

OPENAI_IMAGE_API = "https://api.openai.com/v1/images/generations"


class DALLE3Provider(BaseImageProvider):
    """
    Concrete DALL-E 3 image generation provider.
    Registered with ImageProviderManager under the name 'dalle3' so that
    settings.IMAGE_PROVIDER="dalle3" (the default) routes here.
    """

    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not set — required for DALL-E 3 image generation."
            )
        self._api_key = settings.OPENAI_API_KEY

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = None,
        response_format: str = "url",
        **kwargs,
    ) -> ImageGenerationResult:
        model_name = model or "dall-e-3"
        payload = {
            "model": model_name,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "response_format": response_format,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(OPENAI_IMAGE_API, json=payload, headers=headers)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"DALL-E 3 API error {response.status_code}: {response.text}"
                )
            data = response.json()

        image_data = data["data"][0]
        return ImageGenerationResult(
            url=image_data.get("url"),
            b64_data=image_data.get("b64_json"),
            provider="dalle3",
            model=model_name,
            raw_response=data,
        )


ImageProviderManager.register_provider("dalle3", DALLE3Provider)
