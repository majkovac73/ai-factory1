from app.core.providers.groq_provider import GroqProvider
import json

class PlannerAgent:

    def __init__(self):
        self.llm = GroqProvider()

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

        response = self.llm.generate(system_prompt + "\n\nINPUT:\n" + prompt)

        try:
            return json.loads(response)
        except:
            return {
                "task_type": task_type,
                "goal": prompt,
                "steps": [prompt]
            }