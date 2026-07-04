import json
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer
from app.core.validation.schema_validator import SchemaValidator


class SEOGeneratorAgent(BaseAgent):
    """
    Etsy Module: SEO Generator

    Takes a product concept (e.g. from ProductGeneratorAgent, Step 55)
    and produces SEO-optimized Etsy listing copy: a keyword-rich title,
    persuasive description, search-term keywords, and structured
    sections. Output is validated against SEOSchema before being
    returned, so malformed output is caught here rather than downstream.
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()
        self.validator = SchemaValidator()

    def generate_seo(self, product: dict, task_input: str = "") -> dict:
        """
        Args:
            product: Product concept dict (product_name, concept,
                     target_audience, materials, differentiation,
                     estimated_price_range) — typically from
                     ProductGeneratorAgent.
            task_input: Original user request, for extra context.

        Returns:
            Dict with keys: valid (bool), data (dict, if valid),
            error (str, if invalid), raw (original LLM output).
        """

        prompt = f"""
You are an Etsy SEO copywriting specialist.

Write high-conversion, SEO-optimized Etsy listing copy for the
following product concept.

Product Name: {product.get('product_name', '')}
Concept: {product.get('concept', '')}
Target Audience: {product.get('target_audience', '')}
Materials: {', '.join(product.get('materials', []))}
Differentiation: {product.get('differentiation', '')}

Original request context: {task_input}

Return ONLY valid JSON with this exact structure:
{{
  "title": "Keyword-rich Etsy title, between 20 and 70 characters",
  "description": "Persuasive, benefit-driven product description",
  "keywords": ["at least 5 relevant Etsy search keywords"],
  "sections": ["Hook", "Benefits", "Features", "Call to Action"]
}}

Rules:
- Title must include primary search keywords naturally, not stuffed
- Description must be specific to this product, not generic
- Keywords must be realistic Etsy search terms buyers would use
- No markdown, no extra text, single JSON object only
"""

        response = self._generate(prompt)
        return self.validator.validate_seo(response)

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'product'
        (dict) and optional 'task_input' keys.
        """
        product = task.get("product", {})
        task_input = task.get("task_input", "")
        return self.generate_seo(product, task_input)