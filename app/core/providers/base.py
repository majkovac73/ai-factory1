from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel

class BaseLLMProvider(ABC):
    """Abstract Base Class defining the unified interface for all model interactions."""
    
    @abstractmethod
    async def generate(
        self, 
        model: str,
        prompt: str, 
        system_prompt: Optional[str] = None, 
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Generate a raw text completion response using the specified model."""
        pass

    @abstractmethod
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
        """Generate a response parsed directly into a Pydantic model structure."""
        pass