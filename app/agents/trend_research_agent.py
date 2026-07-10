"""
TrendResearchAgent — step 88 (schema hardened in step 90, format allow-list
tied to real pipeline capability in step 91).

Thin orchestrator used by AutonomyWorker. Calls ResearchAgent + IntelligenceAgent
to surface a broad market opportunity, then a third LLM call translates that
opportunity into ONE specific, nameable, buildable product tied to a concrete
product_format the pipeline can actually produce — never a vague market
sentence, and never a concept (like an interactive app) that no amount of
image generation could ever fulfill.

Returns a dict with product_name, product_format, description,
target_audience, page_count (only meaningful for pdf_planner_or_guide), and
confidence, or None if nothing promising / valid was produced.
"""
import json
import logging

from app.agents.base_agent import BaseAgent
from app.agents.market_intelligence.research import ResearchAgent
from app.agents.market_intelligence.intelligence import IntelligenceAgent
from app.agents.product_viability_critic import ProductViabilityCriticAgent
from app.core.product_formats import PRODUCT_FORMATS
from app.core.utils.json_sanitizer import JSONSanitizer
from config import settings

logger = logging.getLogger("ai-factory")

_RESEARCH_TOPIC = "Etsy digital download and print-on-demand trending products"
_RESEARCH_SCOPE = "trends"

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

# Multi-item language — the fix here is enabling genuinely richer SINGLE
# products (a real multi-page planner, a real sticker sheet), not
# reintroducing "a collection of everything" under a new name.
_MULTI_ITEM_MARKERS = (
    "bundle",
    "bundles",
    " set of",
    "gift set",
    " kit",
    "collection",
    "pack of",
    "starter pack",
    "assortment",
)

_FORMAT_LIST = sorted(PRODUCT_FORMATS.keys())


class TrendResearchAgent(BaseAgent):

    MAX_CONCEPT_ATTEMPTS = 3

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self._research = ResearchAgent(provider, model)
        self._intelligence = IntelligenceAgent(provider, model)
        self._critic = ProductViabilityCriticAgent(provider, model)
        self.sanitizer = JSONSanitizer()
        # A-3: recent shop products, loaded per run() for dedup. Empty by default
        # so direct _propose_product() calls (tests) don't dedup against the DB.
        self._recent_products: list = []

    def run(self) -> dict | None:
        """
        Run a market research + synthesis + concept-specification cycle.
        Returns {'product_name', 'product_format', 'description',
        'target_audience', 'page_count', 'confidence'} or None.
        """
        from app.services.trend_data_service import TrendDataService, TrendDataFetchError

        try:
            trend_data = TrendDataService().get_real_trend_signals()
        except TrendDataFetchError as e:
            logger.error(
                f"TrendResearchAgent: real trend data fetch failed, aborting "
                f"cycle rather than falling back to guessed data: {e}"
            )
            return None

        try:
            research = self._research.research(_RESEARCH_TOPIC, _RESEARCH_SCOPE, real_trend_data=trend_data)
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

        # A-3: load recent shop products so the concept generator avoids
        # proposing near-duplicates (which cannibalize Etsy search and waste the
        # full build cost).
        try:
            from app.services.task_service import TaskService
            self._recent_products = TaskService().recent_product_titles(50)
        except Exception as e:
            logger.warning(f"TrendResearchAgent: could not load recent products for dedup: {e}")
            self._recent_products = []

        insight = opportunities[0]
        product = self._propose_product(insight, intel.get("confidence", "low"))
        if not product:
            logger.error(
                "TrendResearchAgent: could not produce a specific, valid, buildable "
                f"product concept after {self.MAX_CONCEPT_ATTEMPTS} attempts"
            )
            return None

        return product

    def _propose_product(self, insight: str, fallback_confidence: str) -> dict | None:
        """
        Translate a broad market insight into one specific, nameable,
        buildable product tied to a real product_format. Retries with the
        rejection reason fed back to the model, up to MAX_CONCEPT_ATTEMPTS
        times.
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
                # A-3: reject near-duplicates of existing shop products BEFORE the
                # (paid) critic call — consumes a retry with dedup feedback.
                dup = self._dedup_error(data)
                if dup:
                    logger.warning(f"TrendResearchAgent: concept attempt {attempt} rejected as duplicate: {dup}")
                    feedback = f"{dup} Propose a clearly different product — different theme AND different wording."
                    continue

                data["confidence"] = data.get("confidence") or fallback_confidence

                # A-2: validate against REAL Etsy buyer data (competition + real
                # prices + winning titles). Attach it so the critic sees the
                # saturation signal and the listing stage can ground price/SEO.
                self._attach_market(data)

                critique = self._critic.critique(data)
                logger.info(
                    f"TrendResearchAgent: viability critique score={critique['score']} "
                    f"passed={critique['passed']} for '{data.get('product_name')}'"
                )
                if critique["passed"]:
                    return data

                logger.warning(
                    f"TrendResearchAgent: concept attempt {attempt} failed viability "
                    f"critique: {critique['reason']}"
                )
                feedback = (
                    f"Your previous concept was schema-valid but rejected as not "
                    f"commercially viable: {critique['reason']}. Propose a genuinely "
                    f"different, more compelling concept that addresses this."
                )
                continue

            logger.warning(f"TrendResearchAgent: concept attempt {attempt} rejected: {error}")
            feedback = (
                f"Your previous answer was rejected: {error}. "
                "Propose a different, more specific product that fixes this."
            )

        return None

    def _build_concept_prompt(self, insight: str, feedback: str) -> str:
        retry_note = f"\n\nIMPORTANT — retry feedback:\n{feedback}" if feedback else ""
        # A-3: list existing shop products so the model doesn't re-propose them.
        dedup_note = ""
        if self._recent_products:
            listed = "; ".join(f"{t} ({fmt})" for t, fmt in self._recent_products[:30])
            dedup_note = (
                "\n\nProducts ALREADY in the shop — your proposal MUST be clearly "
                "different from ALL of these (different theme AND different wording), "
                f"not a variation of them:\n{listed}"
            )
        return f"""
You are a product strategist for a solo Etsy seller. Your pipeline can ONLY
produce products in these exact formats — nothing else is buildable:

  Single-image digital:
    - single_print (poster/wall art/quote print)
    - coloring_page
    - greeting_card_design
    - phone_wallpaper

  Multi-image digital:
    - pdf_planner_or_guide (a real multi-page PDF — must set page_count,
      an integer from 1 to {settings.MAX_PDF_PAGES})
    - sticker_sheet_design (ONE image containing multiple sticker designs
      laid out on a single sheet — still one product, one image)

  Multi-image print-on-demand:
    - pod_apparel_design (one core design printed on apparel/merchandise;
      the pipeline generates additional listing photos separately)

Given this market insight, propose ONE specific, nameable, buildable product
that fits EXACTLY ONE of the formats above — NOT a market strategy, NOT a
category, NOT a bundle/set/kit/collection of multiple items, and NOT
anything requiring software/interactivity (no apps, no AR, no "tools" —
only something a static image or PDF can actually be).

Market insight:
{insight}{dedup_note}

Return ONLY valid JSON with this structure:
{{
  "product_name": "a specific, concrete product name, e.g. 'Plant Parent Weekly Care Planner'",
  "product_format": "one of: {', '.join(_FORMAT_LIST)}",
  "page_count": <integer, ONLY meaningful/required if product_format is pdf_planner_or_guide, otherwise omit or set to 1>,
  "description": "1-2 sentences specific to THIS item, mentioning it by name",
  "target_audience": "who this is for",
  "confidence": "high/medium/low"
}}{retry_note}
"""

    def _attach_market(self, data: dict) -> None:
        """A-2: look up real Etsy market data for the concept and attach it to
        `data['market']`. Best-effort — never raises, never blocks."""
        try:
            import asyncio
            from app.services.etsy_market_service import EtsyMarketService
            # Use the product name (minus format noise) as the search phrase.
            keywords = (data.get("product_name") or "").strip()
            if not keywords:
                return
            market = asyncio.run(EtsyMarketService().validate_concept(keywords))
            if market:
                data["market"] = market
                logger.info(
                    f"TrendResearchAgent: market for '{keywords}': "
                    f"competition={market.get('competition_count')}, p50={market.get('price_p50')}"
                )
        except Exception as e:
            logger.warning(f"TrendResearchAgent: market attach failed: {e}")

    DEDUP_RATIO = 0.75

    def _dedup_error(self, data: dict) -> str | None:
        """A-3: return a rejection reason if the concept's name is too similar
        (difflib ratio > DEDUP_RATIO) to a recent shop product of the SAME
        format, else None."""
        import difflib
        name = (data.get("product_name") or "").strip().lower()
        fmt = data.get("product_format")
        if not name:
            return None
        for title, ttype in (self._recent_products or []):
            if ttype != fmt:
                continue
            ratio = difflib.SequenceMatcher(None, name, str(title).strip().lower()).ratio()
            if ratio > self.DEDUP_RATIO:
                return (
                    f"This concept ('{data.get('product_name')}') is too similar to an "
                    f"existing shop product ('{title}') (similarity {ratio:.2f})."
                )
        return None

    def _validate_product(self, data: dict) -> str | None:
        """Return an error string if data isn't a valid, specific, buildable product concept, else None."""
        if not isinstance(data, dict):
            return "response was not a JSON object"

        name = (data.get("product_name") or "").strip()
        if not name:
            return "product_name is missing or empty"

        lowered = name.lower()
        for marker in _STRATEGY_MARKERS:
            if marker in lowered:
                return f"product_name reads as a strategy/category ('{marker}'), not a specific product"
        for marker in _MULTI_ITEM_MARKERS:
            if marker in lowered:
                return f"product_name implies multiple distinct items ('{marker.strip()}'), not one specific product"

        product_format = data.get("product_format")
        if product_format not in PRODUCT_FORMATS:
            return f"product_format must be one of {_FORMAT_LIST}, got {product_format!r}"

        description = (data.get("description") or "").strip()
        if not description:
            return "description is missing or empty"
        if name.lower() not in description.lower():
            return "description does not reference the specific product_name"

        # C-1: reject any concept that references a trademarked brand / character
        # / celebrity / franchise — an existential Etsy-suspension risk.
        from app.core.trademark_screen import screen as _tm_screen
        tm_hit = _tm_screen(name, description)
        if tm_hit:
            return (
                f"concept references a trademarked/branded term ('{tm_hit}') — this is a "
                "legal/IP risk and must be avoided; propose a wholly original concept with "
                "no brand, character, celebrity, franchise, or sports references"
            )
        lowered_desc = description.lower()
        for marker in _MULTI_ITEM_MARKERS:
            if marker in lowered_desc:
                return f"description implies multiple distinct items ('{marker.strip()}'), not one specific product"

        if product_format == "pdf_planner_or_guide":
            page_count = data.get("page_count")
            if not isinstance(page_count, int) or isinstance(page_count, bool):
                return "page_count is required for pdf_planner_or_guide and must be an integer"
            if page_count < 1:
                return "page_count must be at least 1"
            if page_count > settings.MAX_PDF_PAGES:
                return f"page_count {page_count} exceeds MAX_PDF_PAGES cap of {settings.MAX_PDF_PAGES}"

        return None
