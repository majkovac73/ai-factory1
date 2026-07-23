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
import re

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
        # 1-2/1-9/1-10: last cycle's persistent-search state (best failed concept).
        self._last_search: dict | None = None
        # seasonal vs evergreen mode for THIS cycle (None = not decided / no
        # enforcement, so direct _propose_product() calls in tests are unaffected).
        self._seasonal_mode: bool | None = None

    def run(self) -> dict | None:
        """
        Run a market research + synthesis + concept-specification cycle.
        Returns {'product_name', 'product_format', 'description',
        'target_audience', 'page_count', 'confidence'} or None.
        """
        from app.services.trend_data_service import TrendDataService, TrendDataFetchError

        # 5-4: load recent shop products (for dedup + the insights block) BEFORE
        # the paid research/intel calls — if the DB is unreachable the cycle would
        # fail anyway, so failing early saves two LLM calls.
        try:
            from app.services.task_service import TaskService
            self._recent_products = TaskService().recent_product_titles(50)
        except Exception as e:
            logger.warning(f"TrendResearchAgent: could not load recent products for dedup: {e}")
            self._recent_products = []
        try:
            trend_data = TrendDataService().get_real_trend_signals()
            self._trend_data = trend_data or {}  # 1-1: for the demand subscore
        except TrendDataFetchError as e:
            logger.error(
                f"TrendResearchAgent: real trend data fetch failed, aborting "
                f"cycle rather than falling back to guessed data: {e}"
            )
            return None

        # #10: record per-cycle trend coverage so it's measurable from the DB (not
        # just stdout). If rising_query_count/matched are usually low, the "grounded
        # in real demand" premise is weak (feeds the mediocrity in #2) — this makes
        # that visible instead of a guess.
        self._record_trend_signal(self._trend_data)

        # A-1/#9: close the learning loop — bias new concepts toward what actually
        # earned / got engagement, away from formats piling up unsold. Loaded AFTER
        # trend_data so that, when internal signal is too sparse to trust (#9), it
        # can steer toward the REAL rising queries in self._trend_data.
        self._insights_block = self._load_insights_block()

        # Decide this cycle's MODE: seasonal (target an in-window occasion) vs
        # evergreen (year-round product). Only SEASONAL_PRODUCT_RATIO of cycles are
        # seasonal, so the catalog keeps a steady evergreen base instead of becoming
        # 100% whatever single occasion happens to be in-window (e.g. all
        # back-to-school in July). This mode gates the research topic, the concept
        # prompt's seasonal block, and evergreen enforcement.
        in_window = []
        try:
            from app.core.seasonality import upcoming_occasions
            in_window = upcoming_occasions()
        except Exception:
            pass
        ratio = float(getattr(settings, "SEASONAL_PRODUCT_RATIO", 0.30))
        self._seasonal_mode = bool(in_window) and random.random() < ratio

        # 2-4: reflect the currently-proposable formats (POD paused → digital only).
        research_topic = (_RESEARCH_TOPIC_WITH_POD
                          if getattr(settings, "POD_APPAREL_ENABLED", False)
                          else _RESEARCH_TOPIC_DIGITAL)
        if self._seasonal_mode:
            occ = random.choice(in_window)["occasion"]
            research_topic = f"Etsy {occ} printable and wall-art products buyers want right now"
        logger.info(f"TrendResearchAgent: mode={'seasonal' if self._seasonal_mode else 'evergreen'}, "
                    f"research topic = {research_topic!r}")

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
        # (recent products + insights block already loaded above — 5-4)

        # 1-2: PERSISTENT search — try EVERY opportunity (not one at random), and
        # if none produce a passer within budget, do ONE fresh research pass on a
        # different topic. 1-3: best-of-pool — build the highest-scoring passer of
        # the whole cycle, not just the first.
        confidence = intel.get("confidence", "low")
        state = self._new_search_state()
        product = self._persistent_search(opportunities, confidence, state)

        if not product:
            # ONE fresh research pass (a different topic) before giving up.
            budget = int(getattr(settings, "CONCEPT_SEARCH_MAX_ATTEMPTS_PER_CYCLE", 15))
            if state["scored"] < budget:
                alt_topic = self._alt_research_topic(research_topic)
                alt_opps = self._research_opportunities(alt_topic, trend_data)
                if alt_opps:
                    logger.info(f"TrendResearchAgent: fresh research pass on {alt_topic!r} ({len(alt_opps)} opportunities)")
                    product = self._persistent_search(alt_opps, confidence, state)

        # 1-9/1-10: stash the cycle's search outcome (best failed concept) for the
        # zero-production alert + near-miss approval queue.
        self._last_search = state

        if not product:
            logger.info(
                f"TrendResearchAgent: cycle exhausted {state['scored']} scored / "
                f"{state['raw']} raw attempts across {state['insights']} insights; "
                f"best total {state['best_total']} "
                f"('{(state.get('best_concept') or {}).get('product_name', '—')}')"
            )
            self._persist_best_failed(state)
            return None

        return product

    # ── 1-2 persistent search ────────────────────────────────────────────────
    @staticmethod
    def _new_search_state() -> dict:
        return {"scored": 0, "raw": 0, "insights": 0, "passers": [],
                "best_total": -1, "best_concept": None, "best_score": None,
                "rejected": []}

    def _persistent_search(self, opportunities: list, confidence: str, state: dict) -> dict | None:
        """1-2: try each opportunity (shuffled top 3) in turn against a SHARED
        cycle budget; 1-3: return the best passer once the pool is built (or a
        clear winner short-circuits)."""
        budget = int(getattr(settings, "CONCEPT_SEARCH_MAX_ATTEMPTS_PER_CYCLE", 15))
        top = list(opportunities[:3])
        random.shuffle(top)
        for insight in top:
            if state["scored"] >= budget:
                break
            state["insights"] += 1
            winner = self._propose_from_insight(insight, confidence, state)
            if winner is not None:
                return winner  # shadow-mode critic pass, or enforce ≥ min+5
        # 1-3: no clear short-circuit winner — build the best passer of the pool.
        if state["passers"]:
            best = max(state["passers"], key=lambda p: p["score"]["total"])
            logger.info(f"TrendResearchAgent: best-of-pool selected '{best['concept'].get('product_name')}' "
                        f"(total {best['score']['total']}) from {len(state['passers'])} passer(s)")
            return best["concept"]
        return None

    @staticmethod
    def _alt_research_topic(prev_topic: str) -> str:
        """A different research topic for the fresh pass — prefer an in-window
        occasion if the first pass was generic, else the reverse."""
        try:
            from app.core.seasonality import upcoming_occasions
            in_window = upcoming_occasions()
            generic = "printable" in (prev_topic or "").lower() and "right now" not in (prev_topic or "").lower()
            if in_window and generic:
                occ = random.choice(in_window)["occasion"]
                return f"Etsy {occ} printable products buyers are searching for right now"
        except Exception:
            pass
        return "underserved Etsy digital printable niches with rising demand and low competition"

    def _research_opportunities(self, topic: str, trend_data: dict) -> list:
        """Run research + intelligence for a topic and return its opportunities."""
        try:
            research = self._research.research(topic, _RESEARCH_SCOPE, real_trend_data=trend_data)
            intel = self._intelligence.synthesize(research)
            return intel.get("opportunities", []) or []
        except Exception as e:
            logger.warning(f"TrendResearchAgent: fresh research pass failed: {e}")
            return []

    def _persist_best_failed(self, state: dict):
        """1-10: record the cycle's best FAILED concept (near-miss) as an analytics
        event so the daily alert can surface it and it can be manually approved."""
        best = state.get("best_concept")
        best_score = state.get("best_score")
        if not best or not best_score:
            return
        try:
            from app.services.analytics_service import AnalyticsService
            min_score = int(getattr(settings, "PRODUCT_MIN_SCORE", 90))
            if state["best_total"] < min_score - 5:
                return  # not a near-miss, don't clutter
            AnalyticsService().record_event(
                event_type="concept_near_miss",
                entity_type="concept",
                entity_id=(best.get("product_name") or "unknown")[:120],
                value=float(state["best_total"]),
                payload={"concept": best, "score_total": state["best_total"],
                         "floors": best_score.get("floors"),
                         "retry_feedback": best_score.get("retry_feedback")},
            )
        except Exception as e:
            logger.warning(f"TrendResearchAgent: could not record near-miss: {e}")

    def _propose_product(self, insight: str, fallback_confidence: str) -> dict | None:
        """Backward-compatible single-insight entry point: run the search for ONE
        insight with a fresh budget and return a winner (or the best passer)."""
        state = self._new_search_state()
        state["insights"] += 1
        winner = self._propose_from_insight(insight, fallback_confidence, state)
        if winner is not None:
            return winner
        if state["passers"]:
            return max(state["passers"], key=lambda p: p["score"]["total"])["concept"]
        return None

    def _propose_from_insight(self, insight: str, fallback_confidence: str, state: dict) -> dict | None:
        """Translate ONE market insight into concept attempts, updating the SHARED
        cycle `state` (scored/raw counters, passer pool, best-so-far, rejected
        list). Returns a concept ONLY on a short-circuit win (shadow critic pass,
        or enforce-mode total >= PRODUCT_MIN_SCORE+5); otherwise None (passers,
        if any, are left in state for best-of-pool selection).

        1-2: JSON/schema/dedup rejects consume a CHEAP `raw` budget (2x scored),
        NOT a scored attempt, so a bad-JSON model can't burn the real budget."""
        enforce = getattr(settings, "PRODUCT_SCORE_ENFORCE", False)
        min_score = int(getattr(settings, "PRODUCT_MIN_SCORE", 90))
        budget = int(getattr(settings, "CONCEPT_SEARCH_MAX_ATTEMPTS_PER_CYCLE", 15))
        raw_cap = budget * 2
        per_insight = self.MAX_CONCEPT_ATTEMPTS  # scored attempts per insight
        short_circuit = min_score + 5

        feedback = ""
        insight_scored = 0
        while state["scored"] < budget and state["raw"] < raw_cap and insight_scored < per_insight:
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
                    state["raw"] += 1
                    logger.warning(f"TrendResearchAgent: raw attempt produced invalid JSON: {e}")
                    feedback = f"Your last response was not valid JSON ({e}). Return ONLY the JSON object."
                    continue

            error = self._validate_product(data)
            if error:
                state["raw"] += 1
                logger.warning(f"TrendResearchAgent: raw attempt rejected: {error}")
                feedback = self._retry_feedback_with_history(
                    state, f"Your previous answer was rejected: {error}. "
                           "Propose a different, more specific product that fixes this.")
                continue

            # A-3: reject near-duplicates BEFORE the (paid) score — cheap retry.
            dup = self._dedup_error(data)
            if dup:
                state["raw"] += 1
                logger.warning(f"TrendResearchAgent: raw attempt rejected as duplicate: {dup}")
                feedback = self._retry_feedback_with_history(
                    state, f"{dup} Propose a clearly different product — different theme AND different wording.")
                continue

            # Evergreen cycle: reject occasion-tied concepts (cheap raw retry) so the
            # SEASONAL_PRODUCT_RATIO actually holds and the shop keeps a year-round base.
            if getattr(self, "_seasonal_mode", None) is False:
                from app.core.seasonality import occasion_for
                occ = occasion_for(data.get("product_name", ""), data.get("description", ""))
                if occ:
                    state["raw"] += 1
                    logger.warning(f"TrendResearchAgent: raw attempt rejected — evergreen cycle got occasion '{occ}'")
                    feedback = self._retry_feedback_with_history(
                        state, f"This is an EVERGREEN cycle — do NOT propose an occasion/holiday product "
                               f"(this one is tied to {occ}). Propose something with STEADY YEAR-ROUND demand, "
                               "not tied to any season, holiday, or occasion.")
                    continue

            data["confidence"] = data.get("confidence") or fallback_confidence
            # A-2: real Etsy market data (competition + prices + winning titles).
            self._attach_market(data)

            # 1-1: composite 0-100 quality score (2 judge LLM calls).
            recent_titles = [t for t, _ in (self._recent_products or [])]
            score = self._score_service.score(
                data, trend_data=self._trend_data, recent_titles=recent_titles)
            state["scored"] += 1
            insight_scored += 1

            # track the cycle's best-so-far (for best-of-pool + near-miss).
            if score["total"] > state["best_total"]:
                state["best_total"] = score["total"]
                state["best_concept"] = data
                state["best_score"] = score

            enforce_now = enforce
            _j = score.get("judges") or {}
            _js = f"{(_j.get('concept_model') or {}).get('score', '?')}/{(_j.get('default_model') or {}).get('score', '?')}" if _j else "gated"
            logger.info(
                f"TrendResearchAgent: product score={score['total']}/100 "
                f"passed={score['passed']} (enforce={enforce_now}) judges={_js} "
                f"floors={score.get('floors')} for '{data.get('product_name')}'"
            )

            if enforce_now:
                if score["passed"]:
                    state["passers"].append({"concept": data, "score": score})
                    if score["total"] >= short_circuit:
                        return data  # 1-3: clear winner, stop early
                    feedback = self._retry_feedback_with_history(state, score["retry_feedback"], data, score)
                    continue
                feedback = self._retry_feedback_with_history(state, score["retry_feedback"], data, score)
                state["rejected"].append({"name": data.get("product_name"), "total": score["total"]})
                logger.warning(f"TrendResearchAgent: below the {min_score} bar: {score['retry_feedback']}")
                continue

            # Shadow mode: the old 6/10 critic still decides while we gather data.
            critique = self._critic.critique(data)
            logger.info(
                f"TrendResearchAgent: [shadow] viability critique score={critique['score']} "
                f"passed={critique['passed']} for '{data.get('product_name')}'"
            )
            if critique["passed"]:
                return data
            state["rejected"].append({"name": data.get("product_name"), "total": score["total"]})
            feedback = self._retry_feedback_with_history(
                state,
                f"Your previous concept was schema-valid but rejected as not commercially "
                f"viable: {critique['reason']}. Propose a genuinely different, more "
                f"compelling concept that addresses this.",
                data, score)

        return None

    @staticmethod
    def _tokens(text: str) -> set:
        """2-5: significant lowercase tokens of a phrase (drops filler)."""
        stop = {"the", "and", "for", "with", "your", "you", "our", "a", "an", "of",
                "to", "in", "on", "printable", "digital", "instant", "download",
                "set", "art", "print", "design"}
        return {w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in (text or "").lower()).split()
                if len(w) > 2 and w not in stop}

    @staticmethod
    def _retry_feedback_with_history(state: dict, base: str, data: dict = None, score: dict = None) -> str:
        """1-7: append this cycle's already-rejected concept names so the model
        doesn't re-propose near-variations of things already scored/rejected."""
        rejected = list(state.get("rejected") or [])
        # include the just-rejected concept too (deduped by name)
        if data is not None and data.get("product_name"):
            entry = {"name": data.get("product_name"), "total": (score or {}).get("total")}
            if all(r.get("name") != entry["name"] for r in rejected):
                rejected = rejected + [entry]
        if not rejected:
            return base
        lines = []
        for r in rejected[-8:]:  # cap at 8 lines
            t = r.get("total")
            lines.append(f"- {r.get('name')}" + (f" (scored {t})" if t is not None else ""))
        block = ("\n\nAlready rejected THIS cycle (do NOT propose these or close "
                 "variations of them):\n" + "\n".join(lines))
        return base + block

    def _seasonal_block(self) -> str:
        """A-7: the dated seasonal block — steer toward an in-window occasion in
        SEASONAL cycles, or explicitly build EVERGREEN (and avoid occasions) in
        evergreen cycles. Default (mode undecided) keeps the seasonal wording."""
        try:
            from app.core.seasonality import seasonal_prompt_block
            mode = "evergreen" if getattr(self, "_seasonal_mode", None) is False else "seasonal"
            return seasonal_prompt_block(mode=mode)
        except Exception:
            return ""

    @staticmethod
    def _proposable_formats() -> list:
        """B-1(b): formats the concept generator may propose — excludes
        pod_apparel_design while POD is paused (POD_APPAREL_ENABLED=False)."""
        formats = list(_FORMAT_LIST)
        # POD formats are proposable when their enable flag is on. The shipping
        # profile a physical listing requires is auto-resolved/created by
        # EtsyShippingService at listing time (and the orchestrator fast-fails a POD
        # task before any generation if that ever fails), so no env var is needed.
        for fmt, flag in (("pod_apparel_design", "POD_APPAREL_ENABLED"),
                          ("pod_mug", "POD_MUG_ENABLED"),
                          ("pod_poster", "POD_POSTER_ENABLED")):
            if not getattr(settings, flag, False):
                formats = [f for f in formats if f != fmt]
        # 7-1: wall_art_set_3 stays paused until validated (multi-piece generation
        # costs ~3x an image; enable deliberately).
        if not getattr(settings, "WALL_ART_SET_ENABLED", False):
            formats = [f for f in formats if f != "wall_art_set_3"]
        # DEEP AUDIT V2 #2: pause formats that blocked 100% of their tasks until
        # their block rate is validated (they only burned generation spend).
        if not getattr(settings, "SEAMLESS_PATTERN_ENABLED", False):
            formats = [f for f in formats if f != "seamless_pattern"]
        if not getattr(settings, "PHONE_WALLPAPER_ENABLED", False):
            formats = [f for f in formats if f != "phone_wallpaper"]
        return formats

    def _margin_guidance_block(self) -> str:
        """#17: bias the concept generator toward higher-margin, less-saturated
        formats. Ranks each proposable format by the net a sale nets after Etsy
        fees (price-band midpoint x (1 - fee)), and explicitly de-prioritizes the
        low-margin saturated formats (coloring_page, phone_wallpaper) that earn
        almost nothing per sale and are hardest to rank as a new shop."""
        try:
            from app.core.product_formats import price_band_for
            fee = (float(getattr(settings, "ETSY_TRANSACTION_FEE_PCT", 0.065))
                   + float(getattr(settings, "ETSY_PAYMENT_FEE_PCT", 0.03)))
            deprioritize = set(getattr(settings, "LOW_MARGIN_DEPRIORITIZE_FORMATS", []) or [])
            ranked = []
            for fmt in self._proposable_formats():
                lo, hi = price_band_for(fmt)
                mid = (lo + hi) / 2.0
                net = round(mid * (1 - fee) - 0.25, 2)  # minus flat payment fee
                ranked.append((fmt, net))
            ranked.sort(key=lambda x: x[1], reverse=True)
            if not ranked:
                return ""
            listed = ", ".join(f"{fmt} (~${net:.2f}/sale)" for fmt, net in ranked)
            avoid = ", ".join(sorted(deprioritize)) if deprioritize else ""
            block = (
                "\n\nMARGIN GUIDANCE (unit economics matter — a $12 planner beats "
                "4 coloring-page sales after fees):\n- Net-per-sale by format, best "
                f"first: {listed}.\n- STRONGLY prefer the higher-margin formats. "
            )
            if avoid:
                block += (
                    f"Only propose a low-margin format ({avoid}) when the niche demand is "
                    "genuinely exceptional (a specific, underserved, clearly-searched audience) — "
                    "otherwise pick a higher-margin format."
                )
            return block
        except Exception as e:
            logger.warning(f"TrendResearchAgent: margin guidance failed: {e}")
            return ""

    # Words that are format/commerce-generic, not THEMES — excluded so the real
    # theme concentration (school, teacher, halloween, garden, ...) surfaces.
    _THEME_STOPWORDS = {
        "with", "from", "your", "that", "this", "and", "for", "the", "set", "pack",
        "printable", "digital", "download", "downloadable", "design", "designs",
        "template", "templates", "print", "prints", "art", "artwork", "sheet",
        "sheets", "page", "pages", "card", "cards", "planner", "guide", "sticker",
        "stickers", "coloring", "wallpaper", "pattern", "poster", "instant", "file",
        "files", "png", "pdf", "kids", "cute", "custom", "customizable", "modern",
        "minimalist", "gift", "gifts", "decor", "wall", "bundle", "collection",
    }

    def _theme_diversity_block(self) -> str:
        """Detect theme monoculture in the existing shop and steer the next concept
        AWAY from over-saturated themes. Without this, a July catalog becomes ~53%
        'back to school' (self-cannibalizing in Etsy search + a seasonal cliff),
        because even 'evergreen' cycles drift toward whatever is trending now."""
        try:
            from collections import Counter
            titles = [t for t, _ in (self._recent_products or [])]
            n = len(titles)
            if n < 8:  # too few products to judge saturation reliably
                return ""
            counts = Counter()
            for t in titles:
                words = {w for w in re.findall(r"[a-z]{4,}", (t or "").lower())
                         if w not in self._THEME_STOPWORDS}
                for w in words:
                    counts[w] += 1
            thresh = float(getattr(settings, "THEME_SATURATION_PCT", 0.25))
            saturated = [(w, c) for w, c in counts.most_common(10) if (c / n) >= thresh]
            if not saturated:
                return ""
            listed = ", ".join(f"'{w}' ({round(100 * c / n)}% of the shop)" for w, c in saturated)
            return (
                "\n\nTHEME SATURATION (CRITICAL diversity constraint): the shop is already "
                f"over-concentrated on these themes: {listed}. More products on these themes "
                "would CANNIBALIZE the existing listings in Etsy search and leave the shop a "
                "one-theme monoculture that collapses when the season ends. Your proposal MUST "
                "be about a COMPLETELY DIFFERENT theme, audience, and occasion — it must NOT "
                "mention or relate to any saturated theme above. Deliberately diversify into an "
                "UNDERSERVED EVERGREEN niche with year-round demand (a different hobby, life "
                "event, aesthetic, profession, or audience)."
            )
        except Exception:
            return ""

    def _coherence_block(self) -> str:
        """Push the shop toward COHERENT collections instead of scattered one-offs.
        A focused shop (several related products per niche) ranks far better in Etsy
        search than 40 unrelated singletons. Soft preference — yields to the demand
        + diversity constraints; never deepens a saturated or proven-dead niche."""
        if not getattr(settings, "SHOP_COHERENCE_ENABLED", True):
            return ""
        try:
            from collections import Counter
            from app.services.niche_memory_service import NicheMemoryService
            prods = self._recent_products or []
            if len(prods) < 6:
                return ""
            nm = NicheMemoryService()
            clusters = Counter()
            for title, _fmt in prods:
                head = " ".join(nm._normalize(title).split()[:2])
                if head:
                    clusters[head] += 1
            if len(clusters) < 3:
                return ""
            singletons = sum(1 for _, c in clusters.items() if c == 1)
            if singletons / len(clusters) < 0.6:  # already reasonably concentrated
                return ""
            mem = nm.load()
            losers = {k.split(':', 1)[-1] for k, v in (mem.get("themes") or {}).items()
                      if v.get("verdict") == "loser"}
            deepen = [n for n, c in clusters.most_common() if 1 <= c <= 4 and n not in losers][:3]
            if not deepen:
                return ""
            return (
                "\n\nSHOP COHERENCE (build COLLECTIONS, not one-offs): the shop is scattered "
                "across many unrelated single products, which ranks poorly in Etsy search — "
                "Etsy favors focused shops with deep, related collections a browsing buyer can "
                "explore. PREFER a NEW, distinct product that DEEPENS one of these existing "
                "niches into a real collection: " + ", ".join(f"'{n}'" for n in deepen) + ". "
                "This is a soft preference — it yields to the demand + diversity constraints; "
                "never deepen a saturated theme or a proven dead end."
            )
        except Exception:
            return ""

    def _demand_grounding_block(self) -> str:
        """Ground the concept in MEASURED demand. The quality gate's demand axis
        scores 4/10 ("no matching trend keyword") whenever a concept doesn't map to
        a keyword with real search interest — which is exactly what drags scores
        below the bar. List the niches with actual demand signal and REQUIRE the
        concept to serve one, so the demand axis (and real buyer traffic) is earned,
        not defaulted."""
        try:
            td = self._trend_data or {}
            it = td.get("interest_trend") or {}
            # keywords that are rising or holding steady = live demand to build on
            live = [kw for kw, info in it.items()
                    if (info or {}).get("direction") in ("rising", "flat")]
            # also surface any rising queries (when pytrends returns them)
            rising = []
            rq = td.get("rising_queries") or {}
            if isinstance(rq, dict):
                for qs in rq.values():
                    rising.extend(str(q) for q in (qs or []))
            if not live and not rising:
                return ""
            parts = []
            if live:
                parts.append("niches with measured search demand: " + ", ".join(sorted(set(live))[:12]))
            if rising:
                parts.append("specific rising searches: " + ", ".join(sorted(set(rising))[:10]))
            return (
                "\n\nDEMAND GROUNDING (REQUIRED — the quality gate scores demand 4/10 "
                "and rejects concepts that don't map to real search demand):\n- "
                + "\n- ".join(parts)
                + "\n- Your concept MUST clearly and specifically serve one of the "
                "demand-backed niches above (use its wording in the product name/"
                "description) so it has real buyers — do NOT invent an arbitrary niche "
                "with no measured demand."
            )
        except Exception:
            return ""

    def _build_concept_prompt(self, insight: str, feedback: str) -> str:
        retry_note = f"\n\nIMPORTANT — retry feedback:\n{feedback}" if feedback else ""
        # B-1(b): only advertise each POD format when it's enabled.
        pod_items = []
        if getattr(settings, "POD_APPAREL_ENABLED", False):
            pod_items.append("    - pod_apparel_design (one core design printed on a t-shirt)")
        if getattr(settings, "POD_MUG_ENABLED", False):
            pod_items.append("    - pod_mug (one design printed on a ceramic coffee mug)")
        if getattr(settings, "POD_POSTER_ENABLED", False):
            pod_items.append("    - pod_poster (one design printed as a physical wall-art poster)")
        pod_line = (
            "  Print-on-demand (real physical products, printed to order):\n"
            + "\n".join(pod_items) + "\n\n"
            if pod_items else ""
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

QUALITY BAR (an independent judge scores this 1-10 on "would a real stranger
BUY this specific item"; only high-quality concepts are built):
- DISTINCTIVE, not generic. A "generic style applied to a common theme" (e.g.
  "retro badge <anything>", "minimalist <common word> quote") scores low. Give it
  a specific, fresh angle a buyer can't already find 50 of.
- LOW competition. Avoid obviously saturated categories (generic quotes, common
  holidays, plain motivational text). Target a specific, underserved sub-niche
  with a clearly-defined buyer.
- CONCRETE and useful/desirable to a real person with a real reason to buy it.

Market insight:
{insight}{self._insights_block}{self._margin_guidance_block()}{self._theme_diversity_block()}{self._coherence_block()}{self._demand_grounding_block()}{self._seasonal_block()}{dedup_note}

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

    @staticmethod
    def _record_trend_signal(trend_data: dict) -> None:
        """#10: persist this cycle's trend coverage as a `trend_signal` analytics
        event {keywords_fetched, rising_query_count, matched}. Best-effort."""
        try:
            rq = (trend_data or {}).get("rising_queries") or {}
            keywords = (trend_data or {}).get("keywords") or []
            rising_query_count = sum(len(v or []) for v in rq.values())
            matched = sum(1 for v in rq.values() if v)  # keywords with >=1 rising query
            from app.services.analytics_service import AnalyticsService
            AnalyticsService().record_event(
                event_type="trend_signal",
                entity_type="cycle",
                entity_id="trend_research",
                value=float(rising_query_count),
                payload={
                    "keywords_fetched": len(keywords),
                    "rising_query_count": rising_query_count,
                    "matched": matched,
                },
            )
        except Exception as e:
            logger.warning(f"TrendResearchAgent: could not record trend_signal: {e}")

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

            total_rev = sum(v or 0 for v in revenue_by_task.values())
            total_views = int(insights.get("total_views", 0) or 0)
            view_floor = int(getattr(settings, "LEARNING_MIN_VIEWS_FOR_SIGNAL", 50))
            # #9: the internal "best so far" view-velocity signal is meaningful ONLY
            # once there's real traffic (or any sale). Below the floor with $0
            # revenue it's noise (chasing 1-view listings), so suppress the internal
            # bias and steer toward EXTERNAL real demand (Google-Trends rising
            # queries) instead of pretending to have "proven" internal winners.
            internal_signal_trustworthy = (total_rev > 0) or (total_views >= view_floor)

            if internal_signal_trustworthy:
                # 2-1: honest label — "earned money" vs "no sales yet, by view velocity".
                top_types = insights.get("top_task_types") or []
                if top_types:
                    parts.append((insights.get("label") or "Best so far:") + " " +
                                 ", ".join(f"{t} ({n})" for t, n in top_types))
                top_kws = insights.get("top_keywords") or []
                if top_kws:
                    parts.append("Themes/keywords in the above: " + ", ".join(k for k, _ in top_kws[:8]))
            else:
                # #9: external-demand steer. Use the REAL rising queries already
                # fetched this cycle (self._trend_data) — never invented data.
                rising = []
                try:
                    # rising_queries is {keyword: [query, ...]} — flatten the values.
                    rq = (self._trend_data or {}).get("rising_queries") or {}
                    if isinstance(rq, dict):
                        for qs in rq.values():
                            rising.extend(str(q) for q in (qs or []))
                    else:  # tolerate a flat list
                        rising = [str(q) for q in rq]
                    rising = rising[:8]
                except Exception:
                    rising = []
                steer = (
                    f"Only {total_views} total listing views so far and no sales — internal "
                    "performance data is too sparse to trust. IGNORE internal 'popular format' "
                    "guesses and ground this concept in REAL external demand"
                )
                if rising:
                    steer += ": prioritize these rising Google-Trends queries — " + ", ".join(rising)
                parts.append(steer + ".")
            if total_rev > 0:
                parts.append(
                    f"Total recorded revenue so far: ${total_rev:.2f}. Bias STRONGLY toward the "
                    "proven themes/formats above — propose a NEW product in that vein, not a copy."
                )

            # 3-5: the bias signal should be DOLLARS, not counts — a $12 planner
            # sale is worth ~4 coloring-page sales. Show per-format net + avg price.
            try:
                pbf = RevenueService().profit_by_format() or {}
                earners = sorted(((f, a) for f, a in pbf.items() if a.get("sales")),
                                 key=lambda kv: kv[1]["net"], reverse=True)
                if earners:
                    parts.append(
                        "PROFIT by format (bias toward the most PROFITABLE, not just the most sold): "
                        + "; ".join(f"{f}: ${a['net']:.2f} net from {a['sales']} sale(s) (avg ${a['avg_price']:.2f})"
                                    for f, a in earners[:5])
                    )
            except Exception:
                pass

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

            # Persistent, self-learned NICHE memory: which themes actually earn
            # views/sales. Empty until there's real traffic (fails safe), then it
            # steers the factory to double down on winners and drop dead ends.
            try:
                from app.services.niche_memory_service import NicheMemoryService
                niche_focus = NicheMemoryService().focus_block()
                if niche_focus:
                    parts.append(niche_focus)
            except Exception:
                pass

            if not parts:
                return ""
            return "\n\nWhat's working in the shop so far (learn from REAL performance):\n- " + "\n- ".join(parts)
        except Exception as e:
            logger.warning(f"TrendResearchAgent: could not load insights: {e}")
            return ""

    def _attach_market(self, data: dict) -> None:
        """A-2 / 1-5: look up real Etsy market data for the concept's NICHE and
        attach it to `data['market']`. Searching the whole product name returns a
        handful of listings (competition looks tiny → inflated 10/10 score); the
        deterministic evidence the gate leans on was systematically wrong. Fix:
        search the NORMALIZED niche query, and take the LARGER of the 3-4 token
        query and the 2-token head-niche count (conservative). Best-effort."""
        try:
            import asyncio
            from app.services.etsy_market_service import EtsyMarketService
            from app.core.search_query import normalize_market_query, head_niche_query
            raw = (data.get("product_name") or "").strip()
            query = normalize_market_query(raw) or raw
            if not query:
                return
            svc = EtsyMarketService()
            market = asyncio.run(svc.validate_concept(query))
            if not market:
                return
            market["query"] = query  # 1-5: auditability

            # second, broader lookup on the 2-token head niche; take the LARGER
            # competition count (a long query undercounts saturation).
            head = head_niche_query(raw)
            if head and head != query:
                try:
                    head_market = asyncio.run(svc.validate_concept(head))
                    if head_market and (head_market.get("competition_count") or 0) > (market.get("competition_count") or 0):
                        market["competition_count"] = head_market.get("competition_count")
                        market["head_query"] = head
                except Exception:
                    pass

            data["market"] = market
            logger.info(
                f"TrendResearchAgent: market for '{query}' (from '{raw}'): "
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
        # 2-5: require the description to reference the product by its SIGNIFICANT
        # tokens (>=2), not the exact verbatim name. Forcing the full name in led
        # to awkward copy ("the Plant Parent Weekly Care Planner planner") and
        # burned retries whenever the model paraphrased ("the Plant Parent planner").
        name_toks = self._tokens(name)
        if name_toks:
            desc_toks = self._tokens(description)
            overlap = len(name_toks & desc_toks)
            need = min(2, len(name_toks))
            if overlap < need:
                return "description does not reference the product (needs >=2 of its key words)"

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
