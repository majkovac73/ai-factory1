from app.agents.base_agent import BaseAgent


class ExecutorAgent(BaseAgent):

    def execute_step(self, step: str, context: str):

        prompt = f"""
You MUST output ONLY valid JSON.

Rules:
- No markdown
- No ``` blocks
- No explanation
- No partial output allowed
- Output must be complete JSON object

If you cannot complete the full JSON, do NOT respond.

Schema:
{{
  "title": "string",
  "description": "string",
  "keywords": ["string"],
  "sections": ["string"]
}}

TASK:
{step}

CONTEXT:
{context}
"""

        return self._generate(prompt)