import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class ExecutorAgent(BaseAgent):

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def execute_step(self, step: str, context: str):

        prompt = f"""
You MUST output ONLY valid JSON.

Your entire response must be a single JSON object, starting with {{ and
ending with }}. Nothing before it, nothing after it.

Rules:
- No markdown
- No ``` blocks
- No explanation
- No partial output allowed
- Output must be one complete, single JSON object

If you cannot complete the full JSON, do NOT respond.

Schema:
{{
  "title": "string",
  "description": "string",
  "keywords": ["string"],
  "sections": ["string"]
}}

Field guidance:
- title: an Etsy-optimized title of 120-140 characters. Front-load the main
  keyword, then pack complementary long-tail keywords a buyer would search,
  separated by commas or pipes. Etsy weights the title heavily in search, so
  use most of the 140 characters (never fewer than 20).
- description: 120+ characters, natural and compelling.
- keywords: at least 3 specific search terms.
- sections: at least 4 section strings.

TASK:
{step}

CONTEXT:
{context}
"""

        output = self._generate(prompt)

        try:
            self.sanitizer.extract(output)
        except Exception as e:
            self.log_service.warning(
                source="ExecutorAgent",
                message="Executor output did not parse as clean JSON",
                payload={"raw_output": output, "error": str(e)},
            )

        return output

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'step' and
        'context' keys and returns the raw executor output string.
        """
        step = task.get("step", "")
        context = task.get("context", "")
        return self.execute_step(step, context)