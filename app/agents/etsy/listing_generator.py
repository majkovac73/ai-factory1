import json
import re
from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer


class ListingGeneratorAgent(BaseAgent):
    """
    Etsy Module: Listing Generator

    Final assembly stage of the Etsy pipeline:
    ProductGeneratorAgent (concept) -> SEOGeneratorAgent (copy) -> HERE.

    Combines a product concept and validated SEO copy into a complete,
    publish-ready Etsy listing: concrete price, category, shipping
    details, and Etsy-compliant tags (max 20 chars each, max 13 tags).
    """

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

    MAX_TAGS = 13
    MAX_TAG_LENGTH = 20

    def _derive_tags(self, keywords: list) -> list:
        """
        Converts SEO keywords into Etsy-compliant tags: max 20 chars
        each, deduplicated, capped at 13 tags (Etsy's actual limit).
        """
        tags = []
        seen = set()
        for kw in keywords:
            tag = kw.strip()[: self.MAX_TAG_LENGTH]
            key = tag.lower()
            if tag and key not in seen:
                tags.append(tag)
                seen.add(key)
            if len(tags) >= self.MAX_TAGS:
                break
        return tags

    def generate_listing(self, product: dict, seo_data: dict) -> dict:
        """
        Args:
            product: Product concept dict from ProductGeneratorAgent
                     (product_name, concept, target_audience, materials,
                     differentiation, estimated_price_range).
            seo_data: Validated SEO dict from SEOGeneratorAgent.validate_seo
                      (title, description, keywords, sections).

        Returns:
            Complete listing dict ready for Step 58 (upload automation).
        """

        prompt = f"""
You are an Etsy listing operations specialist.

Given this product and its SEO copy, determine the remaining listing
metadata: a concrete price point, Etsy category, and shipping details.

Product Name: {product.get('product_name', '')}
Concept: {product.get('concept', '')}
Materials: {', '.join(product.get('materials', []))}
Estimated Price Range: {product.get('estimated_price_range', '')}

SEO Title: {seo_data.get('title', '')}

Return ONLY valid JSON with this exact structure:
{{
  "price": 0.00,
  "currency": "USD",
  "category": "Most relevant Etsy category path, e.g. Home & Living > Home Decor",
  "quantity": 1,
  "processing_time_days": "e.g. 3-5",
  "shipping_notes": "Brief shipping/packaging note"
}}

Rules:
- price must be a single realistic number within or near the estimated range
- category must be a real, plausible Etsy category path
- No markdown, no extra text, single JSON object only
"""

        response = self._generate(prompt)

        try:
            metadata = json.loads(response)
        except Exception:
            try:
                metadata = self.sanitizer.extract(response)
            except Exception:
                metadata = {
                    "price": None,
                    "currency": "USD",
                    "category": "Uncategorized",
                    "quantity": 1,
                    "processing_time_days": "3-5",
                    "shipping_notes": "",
                }

        listing = {
            "product_name": product.get("product_name", ""),
            "title": seo_data.get("title", ""),
            "description": seo_data.get("description", ""),
            "tags": self._derive_tags(seo_data.get("keywords", [])),
            "sections": seo_data.get("sections", []),
            "materials": product.get("materials", []),
            "target_audience": product.get("target_audience", ""),
            "price": metadata.get("price"),
            "currency": metadata.get("currency", "USD"),
            "category": metadata.get("category", "Uncategorized"),
            "quantity": metadata.get("quantity", 1),
            "processing_time_days": metadata.get("processing_time_days", "3-5"),
            "shipping_notes": metadata.get("shipping_notes", ""),
        }

        return listing

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with 'product'
        and 'seo_data' keys.
        """
        product = task.get("product", {})
        seo_data = task.get("seo_data", {})
        return self.generate_listing(product, seo_data)