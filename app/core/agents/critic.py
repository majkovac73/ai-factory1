import asyncio
import json
from app.core.providers.manager import ProviderManager
from app.core.utils.json_sanitizer import JSONSanitizer
from config import settings


class CriticAgent:

    def __init__(self, provider=None):
        self.llm = provider or ProviderManager.get_provider()
        self.model = settings.DEFAULT_MODEL
        self.sanitizer = JSONSanitizer()

    def review(self, output: dict, task_type: str, task_input: str) -> dict:

        prompt = f"""
You are a quality critic for AI-generated Etsy copy.

Evaluate the following output against the task request and SEO conversion goals.

Task type: {task_type}
User input: {task_input}

Output JSON:
{json.dumps(output, ensure_ascii=False)}

Return ONLY valid JSON with these fields:
{{
  "valid": true or false,
  "score": 0-100,
  "issues": ["list any quality problems"],
  "recommendation": "short revision guidance"
}}
"""

        response = asyncio.run(self.llm.generate(model=self.model, prompt=prompt))

        try:
            return json.loads(response)
        except Exception:
            pass

        try:
            parsed = self.sanitizer.extract(response)
            return parsed
        except Exception:
            return {
                "valid": False,
                "score": 0,
                "issues": ["Critic failed to parse model output."],
                "recommendation": "Ensure the critic returns valid JSON."
            }