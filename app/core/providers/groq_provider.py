import os
from groq import Groq

class GroqProvider:

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise Exception("GROQ_API_KEY missing")

        self.client = Groq(api_key=api_key)
        self.last_usage = None

    def generate(self, prompt: str) -> str:

        response = self.client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,        # ✅ FIX #2 HERE
            temperature=0.2        # optional stability improvement
        )

        usage = getattr(response, "usage", None)
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        } if usage else None

        return response.choices[0].message.content