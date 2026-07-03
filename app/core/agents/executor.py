import asyncio
from app.core.providers.manager import ProviderManager
from config import settings


class ExecutorAgent:

    def __init__(self, provider=None):
        self.llm = provider or ProviderManager.get_provider()
        self.model = settings.DEFAULT_MODEL

    def execute_step(self, step: str, context: str):

        prompt = f"""
You MUST output ONLY valid JSON.

Rules:
- No markdown
- No ``` blocks
- No explanation
- No partial output allowed
- Output must be complete JSON object

If you cannot complete the full JSON, do NOT respond.

Schema:
{{
  "title": "string",
  "description": "string",
  "keywords": ["string"],
  "sections": ["string"]
}}

TASK:
{step}

CONTEXT:
{context}
"""

        return asyncio.run(self.llm.generate(model=self.model, prompt=prompt))