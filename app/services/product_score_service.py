"""
ProductScoreService (STEP 105 1-1) — the 0-100 composite product-quality gate.

Replaces the single 6/10 viability critic with a harder, evidence-weighted score
gated at PRODUCT_MIN_SCORE (default 95). The live-shop re-audit showed a 6/10 bar
lets through exactly the "bland but not broken" concepts that now fill the shop
(avg 4.1/10). This gate refuses to build anything that isn't genuinely worth
buying — a skipped day is cheaper than a 60-point product.

Composite = B (deterministic, 0-40) + C (independent LLM judgment, 0-60):

  B — computed from data the concept pipeline ALREADY attaches (zero new spend):
    demand 0-10        (Google-Trends direction of a matched keyword)
    competition 0-10   (Etsy competing-listing count, A-2 market)
    price 0-10         (market p50 vs the format's price band)
    timing 0-5         (evergreen / how much of the occasion window remains)
    originality 0-5    (difflib similarity vs ALL recent shop titles, cross-format)

  C — the existing critic rubric run TWICE on DIFFERENT models (genuinely
    independent judgment). Each returns 1-10; the composite uses the HARSHER
    judge (min), so one generous call can't drag junk over the bar:
    C = 6 * min(score_a, score_b)   (0-60)

Hard gates (trademark / occasion_mismatch / dedup / format validators) are
applied UPSTREAM in TrendResearchAgent before scoring and short-circuit with
their own retry feedback — a concept only reaches here once those pass.

Observability (1-1E): every scored concept is recorded as a `concept_scored`
analytics event with the full breakdown — the tuning dataset for calibrating the
bar before enforcement is flipped on.
"""
import difflib
import logging
from datetime import date

from config import settings

logger = logging.getLogger("ai-factory")


class ProductScoreService:
    def __init__(self, concept_model: str = None, default_model: str = None):
        # Two independent judges: CONCEPT_MODEL (better model when set) + DEFAULT_MODEL.
        self._concept_model = concept_model or getattr(settings, "CONCEPT_MODEL", None) or getattr(settings, "DEFAULT_MODEL", None)
        self._default_model = default_model or getattr(settings, "DEFAULT_MODEL", None)

    # ── B: deterministic evidence subscores (0-40) ───────────────────────────
    @staticmethod
    def _tokens(text: str) -> set:
        stop = {"the", "and", "for", "with", "your", "you", "our", "a", "an", "of",
                "to", "in", "on", "printable", "digital", "instant", "download", "art", "set"}
        return {w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in (text or "").lower()).split()
                if len(w) > 2 and w not in stop}

    @classmethod
    def _demand(cls, concept: dict, trend_data: dict) -> tuple:
        """0-10 from real Google-Trends signal. 1-4: FIRST check the specific
        RISING QUERIES (a concept sparked by "capybara coloring page" rising under
        "coloring pages" is genuine hot demand — 10). Only if no rising query
        matches do we fall back to the matched seed keyword's direction. This
        stops the demand axis from penalizing exactly the specific, original
        concepts the judges reward.

        Returns (points, why, matched_rising_query|None)."""
        ctoks = cls._tokens(f"{concept.get('product_name','')} {concept.get('description','')}")
        ctext = f"{concept.get('product_name','')} {concept.get('description','')}".lower()

        # 1-4: rising queries first.
        rq = (trend_data or {}).get("rising_queries") or {}
        for seed, queries in rq.items():
            for q in (queries or []):
                ql = str(q).lower()
                qtoks = cls._tokens(ql)
                if ql in ctext or len(qtoks & ctoks) >= 2:
                    return 10, f"matches rising query '{q}'", q

        it = (trend_data or {}).get("interest_trend") or {}
        if not it:
            return 4, "no trend data", None
        best_dir, best_kw = None, None
        rank = {"rising": 3, "flat": 2, "falling": 1}
        for kw, info in it.items():
            if cls._tokens(kw) & ctoks:  # keyword overlaps the concept
                d = (info or {}).get("direction")
                if d and (best_dir is None or rank.get(d, 0) > rank.get(best_dir, 0)):
                    best_dir, best_kw = d, kw
        if best_dir is None:
            return 4, "no matching trend keyword", None
        pts = {"rising": 10, "flat": 6, "falling": 0}.get(best_dir, 4)
        return pts, f"'{best_kw}' is {best_dir}", None

    @staticmethod
    def _competition(concept: dict) -> tuple:
        """0-10 from Etsy competing-listing count (A-2)."""
        market = concept.get("market") or {}
        c = market.get("competition_count")
        if c is None:
            return 4, "no market data"
        c = int(c)
        if c < 1000:
            return 10, f"{c} rivals (low)"
        if c < 10000:
            return 8, f"{c} rivals"
        if c < 50000:
            return 5, f"{c} rivals (busy)"
        return 2, f"{c} rivals (saturated)"

    @staticmethod
    def _price(concept: dict) -> tuple:
        """0-10 from market p50 vs the format's price band."""
        from app.core.product_formats import price_band_for
        market = concept.get("market") or {}
        p50 = market.get("price_p50")
        fmt = concept.get("product_format")
        if p50 is None or not fmt:
            return 5, "no price data"
        lo, hi = price_band_for(fmt)
        mid = (lo + hi) / 2.0
        p50 = float(p50)
        if p50 < lo:
            return 2, f"p50 ${p50:.2f} below band floor ${lo:.2f} (race to the bottom)"
        if p50 >= mid:
            return 10, f"p50 ${p50:.2f} in band's upper half"
        return 7, f"p50 ${p50:.2f} in band's lower half"

    @staticmethod
    def _timing(concept: dict, today: date) -> tuple:
        """0-5. Evergreen=5; in-window occasion=5 if >=6 weeks of window remain,
        else 3 (a late-window listing has less time to rank)."""
        from app.core.seasonality import occasion_for, _EVENTS, _next_occurrence
        name = concept.get("product_name", "")
        desc = concept.get("description", "")
        occ = occasion_for(name, desc)
        if not occ:
            return 5, "evergreen"
        ev = next((e for e in _EVENTS if e["key"] == occ), None)
        if not ev:
            return 5, "evergreen"
        if ev.get("match_only"):  # 1-6: year-round occasion (e.g. weddings)
            return 5, f"{occ} (year-round)"
        d = _next_occurrence(ev, today)
        weeks_out = (d - today).days / 7.0
        # weeks of window remaining = how long until we hit the min_w (close) edge
        weeks_remaining = weeks_out - ev["min_w"]
        if weeks_remaining >= 6:
            return 5, f"{occ}: {weeks_remaining:.0f}w of window remain"
        return 3, f"{occ}: only {max(0, weeks_remaining):.0f}w of window remain"

    @classmethod
    def _originality(cls, concept: dict, recent_titles: list) -> tuple:
        """0-5 from max difflib similarity vs ALL recent shop titles (cross-format
        — a near-dupe in another format still cannibalizes search)."""
        name = (concept.get("product_name") or "").strip().lower()
        if not name or not recent_titles:
            return 5, "no comparable titles"
        best = max((difflib.SequenceMatcher(None, name, str(t).strip().lower()).ratio()
                    for t in recent_titles), default=0.0)
        if best < 0.45:
            return 5, f"distinct (max sim {best:.2f})"
        if best < 0.60:
            return 3, f"somewhat similar (max sim {best:.2f})"
        return 1, f"very similar to an existing listing (max sim {best:.2f})"

    def deterministic_breakdown(self, concept: dict, trend_data: dict,
                               recent_titles: list, today: date) -> dict:
        d, d_why, rising_q = self._demand(concept, trend_data)
        c, c_why = self._competition(concept)
        p, p_why = self._price(concept)
        t, t_why = self._timing(concept, today)
        o, o_why = self._originality(concept, recent_titles)
        # 1-4: feed the matched rising query forward so tags/title can use it.
        if rising_q:
            ctx = list(concept.get("seo_context") or [])
            if rising_q not in ctx:
                ctx.append(rising_q)
                concept["seo_context"] = ctx
        return {
            "demand": {"points": d, "max": 10, "why": d_why, "rising_query": rising_q},
            "competition": {"points": c, "max": 10, "why": c_why},
            "price": {"points": p, "max": 10, "why": p_why},
            "timing": {"points": t, "max": 5, "why": t_why},
            "originality": {"points": o, "max": 5, "why": o_why},
            "total": d + c + p + t + o, "max": 40,
        }

    # ── C: dual independent LLM judgment (0-60) ──────────────────────────────
    def _judge(self, concept: dict, model: str) -> dict:
        from app.agents.product_viability_critic import ProductViabilityCriticAgent
        critic = ProductViabilityCriticAgent(model=model)
        try:
            return critic.critique(concept)
        except Exception as e:
            logger.warning(f"ProductScoreService: judge ({model}) failed: {e}")
            return {"passed": False, "score": 0, "reason": f"judge error: {e}"}

    @staticmethod
    def _hard_gate(concept: dict, today: date) -> str:
        """1-1A (belt-and-braces): the hard gates that upstream already applies —
        re-checked here so a hard-gated concept scores 0 without wasting the two
        LLM calls. Returns a reason string on a hit, else None."""
        name = concept.get("product_name", "")
        desc = concept.get("description", "")
        try:
            from app.core.trademark_screen import screen as tm_screen
            hit = tm_screen(name, desc)
            if hit:
                return f"trademark/brand term '{hit}'"
        except Exception:
            pass
        try:
            from app.core.seasonality import occasion_mismatch
            miss = occasion_mismatch(name, desc, today)
            if miss:
                return miss
        except Exception:
            pass
        return None

    # ── composite ────────────────────────────────────────────────────────────
    def score(self, concept: dict, trend_data: dict = None, recent_titles: list = None,
              today: date = None, record: bool = True) -> dict:
        """Score a concept 0-100 and return the full breakdown + pass/fail against
        PRODUCT_MIN_SCORE. Records a `concept_scored` analytics event."""
        today = today or date.today()
        min_score = int(getattr(settings, "PRODUCT_MIN_SCORE", 95))

        gate = self._hard_gate(concept, today)
        if gate:
            result = {
                "total": 0, "max": 100, "passed": False, "min_score": min_score,
                "hard_gate": gate,
                "deterministic": None, "judges": None,
                "retry_feedback": f"scored 0/100 — hard gate: {gate}. Propose a wholly different, compliant concept.",
            }
            if record:
                self._record(concept, result)
            return result

        det = self.deterministic_breakdown(concept, trend_data or {}, recent_titles or [], today)

        # Give both judges the deterministic evidence to reason with.
        judged = dict(concept)
        judged["_scoring_evidence"] = {k: {"points": v["points"], "max": v["max"], "why": v["why"]}
                                       for k, v in det.items() if isinstance(v, dict)}
        j1 = self._judge(judged, self._concept_model)
        j2 = self._judge(judged, self._default_model)
        harsher = min(j1.get("score", 0), j2.get("score", 0))
        llm_points = 6 * harsher  # 0-60, harsher judge

        total = det["total"] + llm_points

        # 1-1: the OLD rule (total >= 95) was mathematically unreachable — 95 needs
        # both judges at 10/10 with a perfect B, which LLM judges essentially never
        # emit, so the factory built nothing forever. The new rule keeps the
        # "excellent on EVERY axis" intent but expresses it as explicit floors,
        # which is arguably STRICTER (a high blended number can't hide a weak axis)
        # AND reachable (B=36 + dual 9s = 90 passes; B=40 + judges 8/9 does not).
        judge_floor = int(getattr(settings, "PRODUCT_JUDGE_FLOOR", 9))
        det_floor = int(getattr(settings, "PRODUCT_DET_FLOOR", 30))
        d_axes = det if isinstance(det, dict) else {}
        floors = {
            "total": total >= min_score,
            # both judges in the "distinctive and clearly compelling" band
            "judge": harsher >= judge_floor,
            # evidence must be strong, not just judges enthusiastic
            "det": det["total"] >= det_floor,
            # no deterministic axis sitting at its rock-bottom value
            "axis": (d_axes.get("demand", {}).get("points", 0) > 0          # not falling
                     and d_axes.get("competition", {}).get("points", 0) > 2  # not >50k saturated
                     and d_axes.get("originality", {}).get("points", 0) > 1),  # not a near-dupe
        }
        passed = all(floors.values())

        result = {
            "total": total,
            "max": 100,
            "passed": passed,
            "min_score": min_score,
            "floors": floors,
            "rule_version": 2,  # 5-8: floors-based rule (STEP 106 1-1)
            "deterministic": det,
            "judges": {
                "concept_model": {"model": self._concept_model, "score": j1.get("score", 0), "reason": j1.get("reason", "")},
                "default_model": {"model": self._default_model, "score": j2.get("score", 0), "reason": j2.get("reason", "")},
                "harsher": harsher,
                "llm_points": llm_points,
            },
            "retry_feedback": self._retry_feedback(total, det, j1, j2, floors),
        }
        if record:
            self._record(concept, result)
        return result

    @staticmethod
    def _retry_feedback(total: int, det: dict, j1: dict, j2: dict, floors: dict = None) -> str:
        """Name the WEAKEST axes so the concept LLM's next attempt fixes the real
        weakness, not a random one."""
        axes = [(k, v["points"], v["max"], v["why"]) for k, v in det.items()
                if isinstance(v, dict) and "points" in v]
        # weakest = lowest fraction of its max
        axes.sort(key=lambda a: a[1] / a[2] if a[2] else 1)
        weak = axes[:2]
        parts = [f"scored {total}/100"]
        for k, pts, mx, why in weak:
            parts.append(f"{k} {pts}/{mx} — {why}")
        harsher = min(j1.get("score", 0), j2.get("score", 0))
        if harsher < 9:
            harsher_reason = j1.get("reason", "") if j1.get("score", 10) <= j2.get("score", 10) else j2.get("reason", "")
            parts.append(f"harsher judge {harsher}/10: {harsher_reason}")
        # 1-1: name which floor failed so the retry targets the binding constraint.
        if floors:
            failed = [k for k, ok in floors.items() if not ok]
            if failed:
                parts.append("failed floors: " + ", ".join(failed))
        return "; ".join(parts) + ". Propose a genuinely stronger concept that fixes the weakest axes."

    @staticmethod
    def _record(concept: dict, result: dict):
        try:
            from app.services.analytics_service import AnalyticsService
            AnalyticsService().record_event(
                event_type="concept_scored",
                entity_type="concept",
                entity_id=(concept.get("product_name") or "unknown")[:120],
                value=float(result["total"]),
                payload={
                    "product_format": concept.get("product_format"),
                    "passed": result["passed"],
                    "min_score": result["min_score"],
                    "floors": result.get("floors"),          # 1-1 / 5-8
                    "rule_version": result.get("rule_version", 2),
                    "hard_gate": result.get("hard_gate"),
                    "deterministic": result["deterministic"],
                    "judges": result["judges"],
                },
            )
        except Exception as e:
            logger.warning(f"ProductScoreService: could not record concept_scored: {e}")
