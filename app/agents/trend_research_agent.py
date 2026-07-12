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
import random

from app.agents.base_agent import BaseAgent
from app.agents.market_intelligence.research import ResearchAgent
from app.agents.market_intelligence.intelligence import IntelligenceAgent
from app.agents.product_viability_critic import ProductViabilityCriticAgent
from app.core.product_formats import PRODUCT_FORMATS
from app.core.utils.json_sanitizer import JSONSanitizer
from config import settings

logger = logging.getLogger("ai-factory")

# 2-4: the research topic must reflect what's actually BUILDABLE right now — with
# POD paused, "print-on-demand" steered research toward unbuildable apparel.
_RESEARCH_TOPIC_DIGITAL = "Etsy digital download printables and wall-art trending products"
_RESEARCH_TOPIC_WITH_POD = "Etsy digital download and print-on-demand trending products"
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

    # 1-1: a 95-point bar needs more shots than the old 6/10 bar (was 3).
    MAX_CONCEPT_ATTEMPTS = 5

    def __init__(self, provider=None, model: str = None):
        # B-5: concept generation uses CONCEPT_MODEL when configured.
        model = model or getattr(settings, "CONCEPT_MODEL", None)
        super().__init__(provider, model)
        self._research = ResearchAgent(provider, model)
        self._intelligence = IntelligenceAgent(provider, model)
        self._critic = ProductViabilityCriticAgent(provider, model)
        # 1-1: the composite 0-100 score gate.
        from app.services.product_score_service import ProductScoreService
        self._score_service = ProductScoreService()
        self.sanitizer = JSONSanitizer()
        # A-3: recent shop products, loaded per run() for dedup. Empty by default
        # so direct _propose_product() calls (tests) don't dedup against the DB.
        self._recent_products: list = []
        # 1-1: the cycle's real trend data (for the deterministic demand subscore).
        self._trend_data: dict = {}
        # A-1: learning-loop insight block injected into the concept prompt.
        self._insights_block: str = ""

    def run(self) -> dict | None:
        """
        Run a market research + synthesis + concept-specification cycle.
        Returns {'product_name', 'product_format', 'description',
        'target_audience', 'page_count', 'confidence'} or None.
        """
        from app.services.trend_data_service import TrendDataService, TrendDataFetchError

        try:
            trend_data = TrendDataService().get_real_trend_signals()
            self._trend_data = trend_data or {}  # 1-1: for the demand subscore
        except TrendDataFetchError as e:
            logger.error(
                f"TrendResearchAgent: real trend data fetch failed, aborting "
                f"cycle rather than falling back to guessed data: {e}"
            )
            return None

        # 1-7: vary the research topic. Every ~3rd cycle, when an occasion is in
        # its buying window, research that occasion directly instead of the same
        # fixed sentence every time — combats the attractor-concept problem that
        # A-3 dedup only fights as a symptom.
        # 2-4: reflect the currently-proposable formats (POD paused → digital only).
        research_topic = (_RESEARCH_TOPIC_WITH_POD
                          if getattr(settings, "POD_APPAREL_ENABLED", False)
                          else _RESEARCH_TOPIC_DIGITAL)
        try:
            from app.core.seasonality import upcoming_occasions
            in_window = upcoming_occasions()
            if in_window and random.random() < 0.34:
                occ = random.choice(in_window)["occasion"]
                research_topic = f"Etsy {occ} printable and wall-art products buyers want right now"
        except Exception:
            pass
        logger.info(f"TrendResearchAgent: research topic = {research_topic!r}")

        try:
            research = self._research.research(research_topic, _RESEARCH_SCOPE, real_trend_data=trend_data)
        except Exception as e:
            logger.error(f"TrendResearchAgent: research step failed: {e}")
            return None

        try:
            intel = self._intelligence.synthesize(research)
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

        # A-1: close the learning loop — bias new concepts toward what actually
        # earned / got engagement, away from formats piling up unsold.
        self._insights_block = self._load_insights_block()

        # 1-7: randomize among the top 3 opportunities instead of always [0].
        idx = random.randrange(min(3, len(opportunities)))
        insight = opportunities[idx]
        logger.info(f"TrendResearchAgent: using opportunity index {idx} of {len(opportunities)}")
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

                # 1-1: composite 0-100 quality score (deterministic evidence +
                # two independent LLM judges). Always computed + recorded as a
                # concept_scored event (the calibration dataset).
                recent_titles = [t for t, _ in (self._recent_products or [])]
                score = self._score_service.score(
                    data, trend_data=self._trend_data, recent_titles=recent_titles)
                enforce = getattr(settings, "PRODUCT_SCORE_ENFORCE", False)
                _j = score.get("judges") or {}
                _js = f"{(_j.get('concept_model') or {}).get('score', '?')}/{(_j.get('default_model') or {}).get('score', '?')}" if _j else "gated"
                logger.info(
                    f"TrendResearchAgent: product score={score['total']}/100 "
                    f"passed={score['passed']} (enforce={enforce}) judges={_js} "
                    f"for '{data.get('product_name')}'"
                )

                if enforce:
                    # 1-1D: the 95 gate decides.
                    if score["passed"]:
                        return data
                    feedback = score["retry_feedback"]
                    logger.warning(f"TrendResearchAgent: attempt {attempt} below the {score['min_score']} bar: {feedback}")
                    continue

                # Shadow mode (1-1E): the old 6/10 critic still decides while we
                # gather concept_scored data.
                critique = self._critic.critique(data)
                logger.info(
                    f"TrendResearchAgent: [shadow] viability critique score={critique['score']} "
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

    @staticmethod
    def _seasonal_block() -> str:
        """A-7: name the occasions buyers are shopping for right now."""
        try:
            from app.core.seasonality import seasonal_prompt_block
            return seasonal_prompt_block()
        except Exception:
            return ""

    @staticmethod
    def _proposable_formats() -> list:
        """B-1(b): formats the concept generator may propose — excludes
        pod_apparel_design while POD is paused (POD_APPAREL_ENABLED=False)."""
        formats = list(_FORMAT_LIST)
        if not getattr(settings, "POD_APPAREL_ENABLED", False):
            formats = [f for f in formats if f != "pod_apparel_design"]
        # 7-1: wall_art_set_3 stays paused until validated (multi-piece generation
        # costs ~3x an image; enable deliberately).
        if not getattr(settings, "WALL_ART_SET_ENABLED", False):
            formats = [f for f in formats if f != "wall_art_set_3"]
        return formats

    def _build_concept_prompt(self, insight: str, feedback: str) -> str:
        retry_note = f"\n\nIMPORTANT — retry feedback:\n{feedback}" if feedback else ""
        # B-1(b): only advertise POD when it's enabled.
        pod_line = (
            "  Multi-image print-on-demand:\n"
            "    - pod_apparel_design (one core design printed on apparel/merchandise;\n"
            "      the pipeline generates additional listing photos separately)\n\n"
            if getattr(settings, "POD_APPAREL_ENABLED", False) else ""
        )
        # 7-1: only advertise the wall-art set format when it's enabled.
        set_line = (
            "  Multi-piece digital SET:\n"
            "    - wall_art_set_3 (a curated set of EXACTLY 3 coordinated wall-art\n"
            "      prints sharing one palette and theme, sold as one listing — a\n"
            "      'gallery wall' set; this is the ONE format where 'set of 3' is allowed)\n\n"
            if getattr(settings, "WALL_ART_SET_ENABLED", False) else ""
        )
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
    - seamless_pattern (a square, edge-to-edge TILEABLE repeating pattern —
      "digital paper" for crafters: scrapbooking, fabric, packaging, gift wrap)

  Multi-image digital:
    - pdf_planner_or_guide (a real multi-page PDF — must set page_count,
      an integer from 1 to {settings.MAX_PDF_PAGES})
    - sticker_sheet_design (ONE image containing multiple sticker designs
      laid out on a single sheet — still one product, one image)

{pod_line}{set_line}Given this market insight, propose ONE specific, nameable, buildable product
that fits EXACTLY ONE of the formats above — NOT a market strategy, NOT a
category, and NOT anything requiring software/interactivity (no apps, no AR,
no "tools" — only something a static image or PDF can actually be). Do NOT
propose a bundle/kit/collection of assorted items; the ONLY multi-item product
allowed is wall_art_set_3 (exactly 3 coordinated prints), and only when it is
listed as an available format above.

Market insight:
{insight}{self._insights_block}{self._seasonal_block()}{dedup_note}

Return ONLY valid JSON with this structure:
{{
  "product_name": "a specific, concrete product name, e.g. 'Plant Parent Weekly Care Planner'",
  "product_format": "one of: {', '.join(self._proposable_formats())}",
  "page_count": <integer, ONLY meaningful/required if product_format is pdf_planner_or_guide, otherwise omit or set to 1>,
  "description": "1-2 sentences specific to THIS item, mentioning it by name",
  "target_audience": "who this is for",
  "confidence": "high/medium/low",
  "text_led": <true ONLY if the product's main visual IS a word/phrase/quote (a quote print, affirmation, name print); else false or omit>,
  "display_text": "<if text_led, the EXACT words to show, correctly spelled — e.g. 'Be Kind'; else omit>"
}}{retry_note}
"""

    def _load_insights_block(self) -> str:
        """A-1: a short block summarizing what's working (best formats/keywords,
        recorded revenue) and the current format mix, injected into the concept
        prompt. Best-effort — returns '' if nothing to say."""
        try:
            from collections import Counter
            from app.services.best_products_service import BestProductsService
            from app.services.revenue_service import RevenueService

            insights = BestProductsService().get_best_product_insights(limit=10)
            revenue_by_task = RevenueService().get_revenue_by_task() or {}
            parts = []

            # 2-1: honest label — "earned money" vs "no sales yet, by view velocity".
            top_types = insights.get("top_task_types") or []
            if top_types:
                parts.append((insights.get("label") or "Best so far:") + " " +
                             ", ".join(f"{t} ({n})" for t, n in top_types))
            top_kws = insights.get("top_keywords") or []
            if top_kws:
                parts.append("Themes/keywords in the above: " + ", ".join(k for k, _ in top_kws[:8]))

            total_rev = sum(v or 0 for v in revenue_by_task.values())
            if total_rev > 0:
                parts.append(
                    f"Total recorded revenue so far: ${total_rev:.2f}. Bias STRONGLY toward the "
                    "proven themes/formats above — propose a NEW product in that vein, not a copy."
                )

            # 2-1 anti-signal: formats piling up listings with ZERO revenue.
            zero = insights.get("zero_revenue_formats") or []
            if zero:
                parts.append(
                    "AVOID these formats — they already have several listings and $0 revenue: "
                    + ", ".join(f"{fmt} ({n})" for fmt, n in zero)
                )

            # Light format budget: current shop mix.
            fmt_counts = Counter(fmt for _t, fmt in (self._recent_products or []))
            if fmt_counts:
                parts.append("Current shop mix: " + ", ".join(f"{fmt}: {n}" for fmt, n in fmt_counts.most_common()))

            if not parts:
                return ""
            return "\n\nWhat's working in the shop so far (learn from REAL performance):\n- " + "\n- ".join(parts)
        except Exception as e:
            logger.warning(f"TrendResearchAgent: could not load insights: {e}")
            return ""

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

        product_format = data.get("product_format")
        if product_format not in PRODUCT_FORMATS:
            return f"product_format must be one of {_FORMAT_LIST}, got {product_format!r}"

        # 7-1: wall_art_set_3 is a legitimate curated SET of exactly 3 coordinated
        # prints, so it is exempt from the multi-item marker ban (which exists to
        # stop "a collection of everything" grab-bags). Every other format stays
        # strictly single-product.
        multi_item_exempt = product_format == "wall_art_set_3"

        lowered = name.lower()
        for marker in _STRATEGY_MARKERS:
            if marker in lowered:
                return f"product_name reads as a strategy/category ('{marker}'), not a specific product"
        if not multi_item_exempt:
            for marker in _MULTI_ITEM_MARKERS:
                if marker in lowered:
                    return f"product_name implies multiple distinct items ('{marker.strip()}'), not one specific product"

        # B-1(b): reject POD while it's paused.
        if product_format not in self._proposable_formats():
            return (
                f"product_format '{product_format}' is currently paused — choose one of "
                f"{self._proposable_formats()}"
            )

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

        # 1-3: reject out-of-season occasion concepts (built too early or too late
        # to ever rank before the occasion) — the mechanical cause of Maj's
        # "seasonal products way too early or too late" complaint.
        from app.core.seasonality import occasion_mismatch
        season_err = occasion_mismatch(name, description)
        if season_err:
            return season_err
        lowered_desc = description.lower()
        if not multi_item_exempt:
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
