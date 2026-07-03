import os
from groq import Groq

class GroqProvider:

    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise Exception("GROQ_API_KEY missing")

        self.client = Groq(api_key=api_key)

    def generate(self, prompt: str) -> str:

        response = self.client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,        # ✅ FIX #2 HERE
            temperature=0.2        # optional stability improvement
        )

        return response.choices[0].message.content