import json
import re

class JSONSanitizer:

    def extract(self, text: str):
        """
        Extracts valid JSON from potentially noisy LLM output.
        
        Strategy (in order):
        1. Remove markdown code fences
        2. Try direct JSON parse (output is already clean)
        3. Find first balanced JSON object by scanning braces
        4. Fail with a descriptive error showing what was attempted
        """

        if not text:
            raise ValueError("Empty LLM output")

        # 1. Remove markdown code fences
        text = re.sub(r"```json", "", text)
        text = re.sub(r"```", "", text)
        text = text.strip()

        if not text:
            raise ValueError("Empty output after markdown removal")

        # 2. Try direct JSON parse (fast path for clean output)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Not valid JSON on its own; try extraction below
            pass

        # 3. Find the first balanced JSON object by scanning braces
        start = text.find("{")

        while start != -1:
            depth = 0
            in_string = False
            escape = False

            for i in range(start, len(text)):
                ch = text[i]

                if ch == '"' and not escape:
                    in_string = not in_string

                if in_string:
                    # Handle escape sequences inside strings
                    if ch == '\\' and not escape:
                        escape = True
                    else:
                        escape = False
                    continue

                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1

                    if depth == 0:
                        candidate = text[start:i+1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            # This candidate didn't parse; try next '{'
                            break

            start = text.find("{", start + 1)

        # 4. Fallback: no valid JSON found
        raise ValueError(
            f"Invalid JSON output: no valid JSON object found. "
            f"Input (first 200 chars): {text[:200]}"
        )

    @staticmethod
    def is_valid_json(text: str) -> bool:
        """
        Quick check: is this text already valid JSON?
        Returns True/False without exceptions.
        """
        if not text or not isinstance(text, str):
            return False
        try:
            json.loads(text)
            return True
        except (json.JSONDecodeError, ValueError):
            return False