import os
from app.core.providers.groq_provider import GroqProvider

class AIRouter:

    def __init__(self):
        self.provider = GroqProvider()

    def call(self, task_type: str, prompt: str):

        full_prompt = f"""
TASK TYPE: {task_type}

Execute this task carefully:

{prompt}
"""

        return self.provider.generate(full_prompt)