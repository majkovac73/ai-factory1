from app.agents.base_agent import BaseAgent


class GeneratorAgent(BaseAgent):

    def generate_step(self, step: str, context: str, role: str, task_type: str) -> str:

        prompt = f"""
You are a {role}.

You must create a HIGH-CONVERSION Etsy product description in strict JSON.

RULES:
- Do NOT write research, analysis, or summaries
- Do NOT explain your process
- Do NOT add markdown, backticks, or extra text
- Output must be a single valid JSON object only
- Output must be ready to paste into an Etsy listing
- Use persuasive, benefit-driven language and emotional triggers

OUTPUT FORMAT:
{{
  "title": "",
  "description": "",
  "keywords": [""],
  "sections": ["Hook", "Benefits", "Features", "Call to Action"]
}}

TASK:
{step}

CONTEXT:
{context}
"""

        return self._generate(prompt)

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'step',
        'context', 'role', and 'task_type' keys.
        """
        step = task.get("step", "")
        context = task.get("context", "")
        role = task.get("role", "copywriter")
        task_type = task.get("task_type", "general")
        return self.generate_step(step, context, role, task_type)