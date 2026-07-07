from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ImageGenerationResult:
    """
    Provider-agnostic result of an image generation call. Concrete
    providers (e.g. DALLE3Provider in Step 67) populate whichever of
    url/b64_data their API returns, so calling code never needs to
    know which provider produced the image.
    """
    url: Optional[str] = None
    b64_data: Optional[str] = None
    provider: str = ""
    model: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


class BaseImageProvider(ABC):
    """
    Abstract base class defining the unified interface for all
    image-generation providers, mirroring BaseLLMProvider
    (app/core/providers/base.py) for text. Concrete providers (DALL-E 3
    in Step 67, others later) implement generate_image() against their
    specific API and return an ImageGenerationResult.
    """

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: Optional[str] = None,
        **kwargs,
    ) -> ImageGenerationResult:
        """Generate an image from a text prompt."""
        pass