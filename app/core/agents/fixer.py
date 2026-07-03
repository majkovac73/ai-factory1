import json
from app.core.providers.groq_provider import GroqProvider

class FixerAgent:

    def __init__(self):
        self.llm = GroqProvider()

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

        return self.llm.generate(prompt)
