from app.core.providers.groq_provider import GroqProvider

class ExecutorAgent:

    def __init__(self):
        self.llm = GroqProvider()

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

        return self.llm.generate(prompt)