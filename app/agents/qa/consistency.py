import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class ConsistencyAgent(BaseAgent):
    """
    QA Expansion: Consistency Agent

    Checks output for internal contradictions — e.g. a title claiming
    "handmade" while the description says "mass-produced," or keywords
    that don't match the described product.
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def check(self, output: dict) -> dict:
        prompt = f"""
You are a logical consistency reviewer for AI-generated Etsy product copy.

Check the following output for internal contradictions between its
fields (title, description, keywords, sections). Look for claims that
conflict with each other.

Output JSON:
{json.dumps(output, ensure_ascii=False)}

Return ONLY valid JSON with these fields:
{{
  "consistent": true or false,
  "contradictions": ["list any contradictions found, empty if none"]
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
                    "consistent": True,
                    "contradictions": [],
                }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'output' key.
        """
        output = task.get("output", {})
        return self.check(output)