import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class ProductGeneratorAgent(BaseAgent):
    """
    Etsy Module: Product Generator

    Generates a concrete, sellable product concept for a given niche or
    market — the raw idea that SEO/listing generation (Steps 56-57) will
    later turn into actual Etsy copy. This agent answers "what should we
    make," not "how should we describe what we already decided to make."
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    def generate_product(self, niche: str, constraints: str = "none") -> dict:
        """
        Args:
            niche: Market/category to generate a product for (e.g. "home decor for cat owners")
            constraints: Optional constraints (materials, budget, production method)

        Returns:
            Dict with product_name, concept, target_audience, materials,
            differentiation, estimated_price_range
        """

        prompt = f"""
You are a product ideation specialist for a handmade/print-on-demand
Etsy shop.

Generate ONE concrete, sellable product concept for the following niche.

Niche: {niche}
Constraints: {constraints}

Return ONLY valid JSON with this structure:
{{
  "product_name": "Short, specific product name",
  "concept": "1-2 sentence description of what the product is",
  "target_audience": "Who this is for",
  "materials": ["material1", "material2"],
  "differentiation": "What makes this stand out from generic competitors",
  "estimated_price_range": "e.g. $18-$28"
}}

Be specific and realistic — avoid vague or generic product ideas.
"""

        response = self._generate(prompt)

        try:
            return json.loads(response)
        except Exception:
            try:
                return self.sanitizer.extract(response)
            except Exception:
                return {
                    "product_name": niche,
                    "concept": response,
                    "target_audience": "general",
                    "materials": [],
                    "differentiation": "",
                    "estimated_price_range": "unknown",
                }

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'niche' and
        optional 'constraints' keys.
        """
        niche = task.get("niche", "")
        constraints = task.get("constraints", "none")
        return self.generate_product(niche, constraints)