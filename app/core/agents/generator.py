import asyncio
from app.core.providers.manager import ProviderManager
from config import settings


class GeneratorAgent:

    def __init__(self, provider=None):
        self.llm = provider or ProviderManager.get_provider()
        self.model = settings.DEFAULT_MODEL

    def generate_step(self, step: str, context: str, role: str, task_type: str) -> str:

        prompt = f"""
You are a {role}.

You must create a HIGH-CONVERSION Etsy product description in strict JSON.

RULES:
- Do NOT write research, analysis, or summaries
- Do NOT explain your process
- Do NOT add markdown, backticks, or extra text
- Output must be a single valid JSON object only
- Output must be ready to paste into an Etsy listing
- Use persuasive, benefit-driven language and emotional triggers

OUTPUT FORMAT:
{{
  "title": "",
  "description": "",
  "keywords": [""],
  "sections": ["Hook", "Benefits", "Features", "Call to Action"]
}}

TASK:
{step}

CONTEXT:
{context}
"""

        return asyncio.run(self.llm.generate(model=self.model, prompt=prompt))