import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class FactCheckAgent(BaseAgent):
    """
    QA Expansion: Fact Check Agent

    Flags claims in generated copy that sound like unverifiable or
    likely-hallucinated factual assertions (e.g. specific certifications,
    awards, or statistics that weren't part of the original input).
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def check(self, output: dict, task_input: str) -> dict:
        prompt = f"""
You are a fact-checking reviewer for AI-generated Etsy product copy.

Original user input (the only source of truth):
{task_input}

Generated output JSON:
{json.dumps(output, ensure_ascii=False)}

Flag any claims in the output that are NOT supported by the original
user input — for example, invented certifications, specific statistics,
awards, or historical claims that weren't mentioned in the input.

Return ONLY valid JSON with these fields:
{{
  "has_unsupported_claims": true or false,
  "flagged_claims": ["list any unsupported claims found, empty if none"]
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
                    "has_unsupported_claims": False,
                    "flagged_claims": [],
                }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'output'
        and 'task_input' keys.
        """
        output = task.get("output", {})
        task_input = task.get("task_input", "")
        return self.check(output, task_input)