import json
from app.agents.base_agent import BaseAgent


class PlannerAgent(BaseAgent):

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

        response = self._generate(system_prompt + "\n\nINPUT:\n" + prompt)

        try:
            return json.loads(response)
        except Exception:
            return {
                "task_type": task_type,
                "goal": prompt,
                "steps": [prompt]
            }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'type' and
        'prompt' keys and returns the generated plan dict.
        """
        task_type = task.get("type") or "general"
        prompt = task.get("prompt", "")
        return self.create_plan(task_type, prompt)