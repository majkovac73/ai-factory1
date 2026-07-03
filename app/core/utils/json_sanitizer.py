import json
import re

class JSONSanitizer:

    def extract(self, text: str):

        if not text:
            raise ValueError("Empty LLM output")

        # 1. Remove markdown code fences
        text = re.sub(r"```json", "", text)
        text = re.sub(r"```", "", text)

        text = text.strip()

        # 2. Try direct JSON parse
        try:
            return json.loads(text)
        except:
            pass

        # 3. Find the first balanced JSON object by scanning braces so
        #    we don't accidentally capture multiple concatenated objects.
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
                    # handle escape sequences inside strings
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
                        except:
                            # parsing failed for this candidate; break to try next '{'
                            break

            start = text.find("{", start + 1)

        # 4. Fallback: no valid JSON found
        raise ValueError(f"Invalid JSON output: {text[:300]}")