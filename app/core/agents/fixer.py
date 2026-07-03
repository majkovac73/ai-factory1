import json
from app.agents.base_agent import BaseAgent


class FixerAgent(BaseAgent):

    def improve(self, current_output: dict, critique: dict, task_type: str, task_input: str, role: str) -> str:

        prompt = f"""
You are a {role} and revision specialist.

Improve the following Etsy product description JSON based on the critique.

Task type: {task_type}
User input: {task_input}

Current output:
{json.dumps(current_output, ensure_ascii=False)}

Critique:
{json.dumps(critique, ensure_ascii=False)}

RULES:
- Keep the same JSON keys
- Make the description persuasive and product-focused
- Eliminate research-style language
- Add stronger conversion language and a clear call to action
- Return ONLY a single valid JSON object
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with
        'current_output', 'critique', 'task_type', 'task_input', and
        'role' keys.
        """
        current_output = task.get("current_output", {})
        critique = task.get("critique", {})
        task_type = task.get("task_type", "general")
        task_input = task.get("task_input", "")
        role = task.get("role", "copywriter")
        return self.improve(current_output, critique, task_type, task_input, role)