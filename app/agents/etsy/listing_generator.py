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

    @staticmethod
    def title_ngrams(titles: list, max_terms: int = 8) -> list:
        """2-4: extract 2-3 word buyer-search phrases from the real winning Etsy
        titles for this niche (A-2 market.top_titles), trademark-filtered
        (competitor titles DO contain brand terms). These are proven-to-rank
        phrases — far better tag padding than product-name fragments."""
        from app.core.trademark_screen import find_trademark
        out, seen = [], set()
        for title in titles or []:
            toks = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in str(title).lower()).split() if len(t) > 2]
            for n in (3, 2):
                for i in range(len(toks) - n + 1):
                    phrase = " ".join(toks[i:i + n])
                    if len(phrase) <= 20 and phrase not in seen and not find_trademark(phrase):
                        seen.add(phrase)
                        out.append(phrase)
                        if len(out) >= max_terms:
                            return out
        return out

    # #7: generic-but-valid evergreen tags, used ONLY as a final backfill so a
    # listing never ships with fewer than 13 tags (an empty slot is a search you
    # can never appear in). Every one is <= 20 chars and a real buyer phrase.
    _FILLER_TAGS = [
        "digital download", "printable art", "instant download", "wall art print",
        "home decor gift", "printable decor", "digital print", "art print gift",
        "downloadable art", "printable gift", "modern wall art", "digital art print",
        "printable wall art",
    ]

    @classmethod
    def _to_valid_tag(cls, candidate: str):
        """#7: normalize a candidate into an Etsy-valid tag — single-spaced,
        stripped, and <= 20 chars WITHOUT cutting mid-word. Etsy matches whole tag
        phrases, so a hard slice to 20 chars ("classroom organizati") matches no
        real query and wastes the slot. Instead drop trailing WHOLE words until it
        fits; return None if no valid (>=3 char) whole-word tag fits, so the slot
        is backfilled with a real phrase rather than a fragment."""
        if not candidate:
            return None
        words = " ".join(str(candidate).split()).strip().split()
        while words and len(" ".join(words)) > cls.MAX_TAG_LENGTH:
            words.pop()  # drop the trailing partial/overflowing word, keep whole words
        tag = " ".join(words).strip()
        if len(tag) < 3 or len(tag) > cls.MAX_TAG_LENGTH:
            return None
        return tag

    @classmethod
    def validate_tags(cls, tags: list) -> list:
        """#7 post-generation gate: every tag must be non-empty, stripped, and
        <= 20 chars (Etsy hard-truncates otherwise). Drops any invalid tag and
        de-dupes. Callers should then have exactly MAX_TAGS."""
        out, seen = [], set()
        for t in tags or []:
            if not isinstance(t, str):
                continue
            tag = t.strip()
            if 3 <= len(tag) <= cls.MAX_TAG_LENGTH and tag == t.strip() and tag.lower() not in seen:
                out.append(tag)
                seen.add(tag.lower())
        return out[: cls.MAX_TAGS]

    def _derive_tags(self, keywords: list, product_name: str = "", extra_terms: list = None) -> list:
        """
        Converts SEO keywords into Etsy-compliant tags: max 20 chars each (WHOLE
        words, never mid-word — #7), deduplicated, and — A-4 — PADDED to a full 13
        tags. Every unused tag slot is a search you can never appear in, so we fill
        them from the product name's 2-3 word phrases, keyword combinations, and a
        final generic-but-valid filler pool. Prefers multi-word phrases (they match
        Etsy phrase searches better than single words).
        """
        tags = []
        seen = set()

        def _add(candidate: str) -> bool:
            tag = self._to_valid_tag(candidate)
            if tag is None:
                return False
            key = tag.lower()
            if key not in seen:
                tags.append(tag)
                seen.add(key)
                return True
            return False

        # 1. The LLM's keywords first (highest intent), multi-word preferred.
        for kw in sorted([k for k in keywords if isinstance(k, str)], key=lambda k: (len(k.split()) < 2, keywords.index(k))):
            _add(kw)
            if len(tags) >= self.MAX_TAGS:
                return tags

        # 2. Caller-provided extra phrases (e.g. real winning-title n-grams, A-2).
        for term in (extra_terms or []):
            if isinstance(term, str):
                _add(term)
                if len(tags) >= self.MAX_TAGS:
                    return tags

        # 3. Pad from 2-3 word phrases built out of the product name tokens.
        tokens = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in (product_name or "").lower()).split() if len(t) > 2]
        phrases = []
        for n in (3, 2):
            for i in range(len(tokens) - n + 1):
                phrases.append(" ".join(tokens[i:i + n]))
        for phrase in phrases:
            _add(phrase)
            if len(tags) >= self.MAX_TAGS:
                return tags

        # 4. Last resort: combine existing keywords with common buyer modifiers.
        modifiers = ["printable", "digital", "wall art", "gift", "decor", "download", "instant"]
        base_words = tokens or [t for kw in keywords if isinstance(kw, str) for t in kw.split()]
        for w in base_words:
            for m in modifiers:
                _add(f"{w} {m}")
                if len(tags) >= self.MAX_TAGS:
                    return self.validate_tags(tags)

        # 5. #7: final generic-but-valid filler so we NEVER under-fill the 13 slots
        # (18/45 live listings used only 3-8 tags — free discoverability lost).
        for f in self._FILLER_TAGS:
            _add(f)
            if len(tags) >= self.MAX_TAGS:
                break
        return self.validate_tags(tags)

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
            "tags": self._derive_tags(
                seo_data.get("keywords", []),
                product_name=product.get("product_name", "") or seo_data.get("title", ""),
                extra_terms=product.get("tag_terms"),
            ),
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