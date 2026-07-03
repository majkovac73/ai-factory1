import asyncio
import json
from app.core.providers.manager import ProviderManager
from config import settings


class PlannerAgent:

    def __init__(self, provider=None):
        self.llm = provider or ProviderManager.get_provider()
        self.model = settings.DEFAULT_MODEL

    def create_plan(self, task_type: str, prompt: str):

        system_prompt = f"""
You are a STRICT planning system.

Return ONLY valid JSON.

NO explanations. NO markdown. NO extra text.

Schema:
{{
  "task_type": "{task_type}",
  "goal": "string",
  "steps": ["step1", "step2", "step3"]
}}

Rules:
- steps must be actionable
- do NOT introduce new products or topics
- stay strictly within user input
"""

        response = asyncio.run(
            self.llm.generate(model=self.model, prompt=system_prompt + "\n\nINPUT:\n" + prompt)
        )

        try:
            return json.loads(response)
        except Exception:
            return {
                "task_type": task_type,
                "goal": prompt,
                "steps": [prompt]
            }