"""
TrendResearchAgent — step 88 (schema hardened in step 90).

Thin orchestrator used by AutonomyWorker. Calls ResearchAgent + IntelligenceAgent
to surface a broad market opportunity, then a third LLM call translates that
opportunity into ONE specific, nameable, buildable product — the broad
"focus on niche markets" style output IntelligenceAgent naturally produces is
never passed downstream as if it were a product concept.

Returns a dict with product_name, product_type, description, target_audience,
and confidence, or None if nothing promising / valid was produced.
"""
import json
import logging

from app.agents.base_agent import BaseAgent
from app.agents.market_intelligence.research import ResearchAgent
from app.agents.market_intelligence.intelligence import IntelligenceAgent
from app.core.utils.json_sanitizer import JSONSanitizer

logger = logging.getLogger("ai-factory")

_RESEARCH_TOPIC = "Etsy digital download and print-on-demand trending products"
_RESEARCH_SCOPE = "trends"

_VALID_PRODUCT_TYPES = {"digital_download", "pod"}

# Strategy/category language markers — a real product_name never reads like
# these; if it does, the LLM described the market instead of naming an item.
_STRATEGY_MARKERS = (
    "niche market",
    "niche markets",
    "various products",
    "variety of products",
    "collection of",
    "focus on",
    "range of products",
    "different types of",
    "wide selection",
    "assortment of",
)


class TrendResearchAgent(BaseAgent):

    MAX_CONCEPT_ATTEMPTS = 3

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self._research = ResearchAgent(provider, model)
        self._intelligence = IntelligenceAgent(provider, model)
        self.sanitizer = JSONSanitizer()

    def run(self) -> dict | None:
        """
        Run a market research + synthesis + concept-specification cycle.
        Returns {'product_name', 'product_type', 'description',
        'target_audience', 'confidence'} or None.
        """
        try:
            research = self._research.research(_RESEARCH_TOPIC, _RESEARCH_SCOPE)
        except Exception as e:
            logger.error(f"TrendResearchAgent: research step failed: {e}")
            return None

        try:
            intel = self._intelligence.synthesize(research, "")
        except Exception as e:
            logger.error(f"TrendResearchAgent: intelligence step failed: {e}")
            return None

        opportunities = intel.get("opportunities", [])
        if not opportunities:
            logger.info("TrendResearchAgent: no opportunities returned by intelligence agent")
            return None

        insight = opportunities[0]
        product = self._propose_product(insight, intel.get("confidence", "low"))
        if not product:
            logger.error(
                "TrendResearchAgent: could not produce a specific, valid product "
                f"concept after {self.MAX_CONCEPT_ATTEMPTS} attempts"
            )
            return None

        return product

    def _propose_product(self, insight: str, fallback_confidence: str) -> dict | None:
        """
        Translate a broad market insight into one specific, nameable product.
        Retries with the rejection reason fed back to the model, the same
        retry -> repair pattern used elsewhere (JSONSanitizer + targeted
        feedback), up to MAX_CONCEPT_ATTEMPTS times.
        """
        feedback = ""
        for attempt in range(1, self.MAX_CONCEPT_ATTEMPTS + 1):
            prompt = self._build_concept_prompt(insight, feedback)
            try:
                response = self._generate(prompt)
            except Exception as e:
                logger.error(f"TrendResearchAgent: concept generation call failed: {e}")
                return None

            try:
                data = json.loads(response)
            except Exception:
                try:
                    data = self.sanitizer.extract(response)
                except Exception as e:
                    logger.warning(f"TrendResearchAgent: attempt {attempt} produced invalid JSON: {e}")
                    feedback = f"Your last response was not valid JSON ({e}). Return ONLY the JSON object."
                    continue

            error = self._validate_product(data)
            if not error:
                data["confidence"] = data.get("confidence") or fallback_confidence
                return data

            logger.warning(f"TrendResearchAgent: concept attempt {attempt} rejected: {error}")
            feedback = (
                f"Your previous answer was rejected: {error}. "
                "Propose a different, more specific product_name that fixes this."
            )

        return None

    def _build_concept_prompt(self, insight: str, feedback: str) -> str:
        retry_note = f"\n\nIMPORTANT — retry feedback:\n{feedback}" if feedback else ""
        return f"""
You are a product strategist for a solo Etsy seller.

Given this market insight, propose ONE specific, nameable, buildable product
that could actually be listed on Etsy today — NOT a market strategy, category,
or description of a whole business.

Market insight:
{insight}

Return ONLY valid JSON with this structure:
{{
  "product_name": "a specific, concrete product name, e.g. 'Plant Parent Weekly Care Planner'",
  "product_type": "digital_download or pod",
  "description": "1-2 sentences specific to THIS item, mentioning it by name",
  "target_audience": "who this is for",
  "confidence": "high/medium/low"
}}{retry_note}
"""

    def _validate_product(self, data: dict) -> str | None:
        """Return an error string if data isn't a valid, specific product concept, else None."""
        if not isinstance(data, dict):
            return "response was not a JSON object"

        name = (data.get("product_name") or "").strip()
        if not name:
            return "product_name is missing or empty"

        lowered = name.lower()
        for marker in _STRATEGY_MARKERS:
            if marker in lowered:
                return f"product_name reads as a strategy/category ('{marker}'), not a specific product"

        product_type = data.get("product_type")
        if product_type not in _VALID_PRODUCT_TYPES:
            return f"product_type must be one of {sorted(_VALID_PRODUCT_TYPES)}, got {product_type!r}"

        description = (data.get("description") or "").strip()
        if not description:
            return "description is missing or empty"
        if name.lower() not in description.lower():
            return "description does not reference the specific product_name"

        return None
