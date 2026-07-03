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