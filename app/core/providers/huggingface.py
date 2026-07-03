import os
import requests

class HuggingFaceProvider:

    def __init__(self):
        self.api_key = os.getenv("HF_API_KEY")

        if not self.api_key:
            raise Exception("HF_API_KEY missing")

        self.api_url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.2"

        self.headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

    def generate(self, prompt: str) -> str:

        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 400,
                "temperature": 0.7
            }
        }

        response = requests.post(
            self.api_url,
            headers=self.headers,
            json=payload
        )

        result = response.json()

        # handle different response formats safely
        if isinstance(result, list) and "generated_text" in result[0]:
            return result[0]["generated_text"]

        if isinstance(result, dict) and "error" in result:
            return f"HF ERROR: {result['error']}"

        return str(result)