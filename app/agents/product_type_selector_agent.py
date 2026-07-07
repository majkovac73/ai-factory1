"""
Product Type Selector Agent — step 81-2d.

Given a task's product concept and the live Printify blueprint catalog,
picks the best-fit blueprint using LLM reasoning. Returns strict JSON
validated via JSONSanitizer.
"""
import json
from typing import Dict, List, Optional

from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class ProductTypeSelectorAgent(BaseAgent):
    """
    Picks the most suitable Printify blueprint for a given product concept.

    Input (run dict keys):
      concept     — product concept text from task.output_data
      blueprints  — list of {id, title} dicts from PrintifyClient.list_blueprints()

    Output:
      {"blueprint_id": <int>}
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def _build_prompt(self, concept: str, blueprints: List[Dict]) -> str:
        catalog_lines = "\n".join(
            f"  {bp['id']}: {bp.get('title', 'Unknown')}" for bp in blueprints
        )
        return f"""You are a print-on-demand product specialist.

Given this product concept, pick the single most suitable Printify product
blueprint from the catalog below. Choose based on product type fit only —
not price or availability.

Product concept:
{concept}

Printify blueprint catalog (id: title):
{catalog_lines}

Return ONLY valid JSON with this exact structure:
{{
  "blueprint_id": <integer blueprint id from the list above>
}}

Rules:
- blueprint_id must be an integer from the catalog above
- No markdown, no explanation, single JSON object only
"""

    def select(self, concept: str, blueprints: List[Dict]) -> Dict:
        prompt = self._build_prompt(concept, blueprints)
        response = self._generate(prompt)

        try:
            result = json.loads(response)
        except Exception:
            try:
                result = self.sanitizer.extract(response)
            except Exception:
                result = {"blueprint_id": blueprints[0]["id"] if blueprints else 0}

        return {"blueprint_id": int(result.get("blueprint_id", 0))}

    def run(self, task: dict) -> dict:
        concept = task.get("concept", "")
        blueprints = task.get("blueprints", [])
        return self.select(concept, blueprints)
