import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer
from config import settings


class ExecutorAgent(BaseAgent):

    def __init__(self, provider=None, model: str = None):
        # B-5: SEO/listing generation uses SEO_MODEL when configured.
        model = model or getattr(settings, "SEO_MODEL", None)
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def execute_step(self, step: str, context: str):

        prompt = f"""
You MUST output ONLY valid JSON.

Your entire response must be a single JSON object, starting with {{ and
ending with }}. Nothing before it, nothing after it.

Rules:
- No markdown
- No ``` blocks
- No explanation
- No partial output allowed
- Output must be one complete, single JSON object

If you cannot complete the full JSON, do NOT respond.

Schema:
{{
  "title": "string",
  "description": "string",
  "keywords": ["string"],
  "sections": ["string"]
}}

Field guidance:
- title: a clean, human-readable Etsy title, 80-130 characters total. The FIRST
  ~40 characters MUST be the primary buyer phrase, written naturally (a shopper
  sees only the first ~40 chars in the search grid, and Etsy's 2026 ranking
  weights them most and penalizes unreadable keyword soup). After that, add 2-3
  complementary long-tail phrases separated by " | ". Do NOT keyword-stuff to 140
  characters — that is the outdated 2020 meta and hurts click-through now.
  Example: "Boho Sunset Wall Art Print | Terracotta Desert Decor | Printable".
- description: open with a compelling hook sentence (<=160 chars, keyword-rich)
  that also reads well as a Google/Etsy snippet, then a short, specific
  paragraph. (Standard "what you get / how it works / terms" sections are added
  automatically — focus on the creative hook and what makes THIS item special.)
- keywords: EXACTLY 13 Etsy tags. Each is a buyer-search PHRASE of 2-3 words,
  <=20 characters, no punctuation, all lowercase. Mix head terms and specific
  long-tail phrases a real buyer would type (e.g. "boho wall art", "nursery
  print", "sage green decor"). Every one of the 13 tag slots is a search you can
  appear in — use all 13, no single-word filler, no duplicates.
- sections: at least 4 section strings.

TASK:
{step}

CONTEXT:
{context}
"""

        output = self._generate(prompt)

        try:
            self.sanitizer.extract(output)
        except Exception as e:
            self.log_service.warning(
                source="ExecutorAgent",
                message="Executor output did not parse as clean JSON",
                payload={"raw_output": output, "error": str(e)},
            )

        return output

    def run(self, task: dict) -> str:
        """
        Standardized entry point. Expects a task dict with 'step' and
        'context' keys and returns the raw executor output string.
        """
        step = task.get("step", "")
        context = task.get("context", "")
        return self.execute_step(step, context)