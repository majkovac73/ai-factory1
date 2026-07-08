import os
import json
from typing import Optional, Type, Any, Dict
from pydantic import BaseModel
from openai import AsyncOpenAI
from app.core.providers.base import BaseLLMProvider

class OpenRouterProvider(BaseLLMProvider):
    def __init__(self, api_key: Optional[str] = None, site_url: Optional[str] = None, site_name: Optional[str] = None):
        """
        Initializes the OpenRouter client using OpenAI compatibility schemas.
        """
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is missing.")
            
        # OpenRouter specific headers for rankings/analytics
        self.extra_headers = {}
        if site_url or os.getenv("OPENROUTER_SITE_URL"):
            self.extra_headers["HTTP-Referer"] = site_url or os.getenv("OPENROUTER_SITE_URL")
        if site_name or os.getenv("OPENROUTER_SITE_NAME"):
            self.extra_headers["X-Title"] = site_name or os.getenv("OPENROUTER_SITE_NAME")

        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
            default_headers=self.extra_headers
        )
        self.last_usage = None

    async def generate(
        self, 
        model: str,
        prompt: str, 
        system_prompt: Optional[str] = None, 
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )

        usage = getattr(response, "usage", None)
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        } if usage else None

        return response.choices[0].message.content

    async def generate_with_images(
        self,
        model: str,
        prompt: str,
        image_data_urls: list,
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        Multimodal completion: sends `prompt` plus one or more images to a
        VISION-capable model (e.g. openai/gpt-4o-mini) and returns the text
        response. `image_data_urls` are full data URLs
        ("data:image/png;base64,...."). Used by ContentQualityService to
        inspect actual generated content — a different capability from image
        GENERATION (which produces images; this consumes them).
        """
        content = [{"type": "text", "text": prompt}]
        for url in image_data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )

        usage = getattr(response, "usage", None)
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        } if usage else None

        return response.choices[0].message.content

    async def generate_structured(
        self,
        model: str,
        prompt: str,
        response_model: Type[BaseModel],
        system_prompt: Optional[str] = None, 
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> BaseModel:
        """
        Requests structured output by passing a JSON schema enforcement payload 
        compatible with OpenRouter's supported models.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            
        # Append clear structural guidance to user prompt as a safety fallback
        schema_instruction = f"\n\nYou MUST respond strictly matching this JSON schema: {json.dumps(response_model.model_json_schema())}"
        messages.append({"role": "user", "content": f"{prompt}{schema_instruction}"})
        
        # Configure JSON response formatting payload
        response_format = {
            "type": "json_object",
            "schema": response_model.model_json_schema()
        }
        
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            **kwargs
        )
        
        raw_content = response.choices[0].message.content
        # Parse output directly back into the requested Pydantic Schema
        return response_model.model_validate_json(raw_content)