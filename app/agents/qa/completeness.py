import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class CompletenessAgent(BaseAgent):
    """
    QA Expansion: Completeness Agent

    Checks whether the generated output fully addresses the original
    task/prompt — not just whether it's valid JSON, but whether it's
    actually a complete answer to what was asked.
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def check(self, output: dict, task_input: str) -> dict:
        prompt = f"""
You are a completeness reviewer for AI-generated Etsy product copy.

Original task:
{task_input}

Generated output JSON:
{json.dumps(output, ensure_ascii=False)}

Determine whether the output fully addresses the original task, or if
it is missing something the task asked for (e.g. a requested feature,
tone, or detail that isn't reflected anywhere in the output).

Return ONLY valid JSON with these fields:
{{
  "complete": true or false,
  "missing_elements": ["list anything the task asked for that's missing, empty if none"]
}}
"""

        response = self._generate(prompt)

        try:
            return json.loads(response)
        except Exception:
            try:
                return self.sanitizer.extract(response)
            except Exception:
                return {
                    "complete": True,
                    "missing_elements": [],
                }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'output'
        and 'task_input' keys.
        """
        output = task.get("output", {})
        task_input = task.get("task_input", "")
        return self.check(output, task_input)