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
  "description": "A detailed 130-200 word description (700-1100 characters) — see the description rules below",
  "keywords": ["at least 5 relevant Etsy search keywords"],
  "sections": ["Hook", "Benefits", "Features", "Call to Action"]
}}

Rules:
- Title must include primary search keywords naturally, not stuffed
- Keywords must be realistic Etsy search terms buyers would use
- No markdown, no extra text, single JSON object only

Description rules (Etsy rewards long, specific, keyword-rich descriptions — thin
descriptions rank and convert worse):
- 130-200 words / 700-1100 characters. Specific to THIS product, never generic.
- FIRST sentence is a benefit-driven hook that contains the PRIMARY keyword
  (Etsy weights the opening line and it's the search/preview snippet).
- Then cover, in short scannable sentences or a simple list:
    * WHAT'S INCLUDED — exact format (PDF/PNG/JPG), page count or dimensions,
      and that it is an INSTANT DIGITAL DOWNLOAD (no physical item is shipped).
    * WHO IT'S FOR — the specific buyer/audience.
    * 2-3 concrete USE CASES or occasions.
    * HOW TO USE IT — download, print at home or at a print shop, common sizes.
- Weave in 4-6 of the target keywords NATURALLY (no keyword stuffing).
- End with a short, warm call to action.
- Do NOT promise physical shipping, custom personalization you can't deliver, or
  trademarked characters/brands.
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