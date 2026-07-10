"""
ProductViabilityCriticAgent — a dedicated, principle-based judgment step
for "would a real stranger actually buy this specific item," independent
of schema/format validity.

This deliberately does NOT use keyword/pattern matching against known bad
examples — that only catches failure modes already seen. Instead it asks
the model to reason about commercial viability from first principles,
the same way a second, independent set of eyes would review a product
before it goes live. It is meant to generalize to product ideas nobody
has proposed yet, not just the ones already caught in review.
"""
import json

from app.agents.base_agent import BaseAgent
from app.core.utils.json_sanitizer import JSONSanitizer
from config import settings


class ProductViabilityCriticAgent(BaseAgent):

    def __init__(self, provider=None, model: str = None, min_score: int = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()
        # Pass/fail is derived from the score against this threshold in code,
        # NOT from the model's own "passed" field — spot-checks showed the model
        # sets an inconsistent, far-too-harsh internal bar (it fails genuinely
        # sellable products by always imagining more differentiation). A numeric
        # threshold is consistent and tunable (settings.VIABILITY_CRITIC_MIN_SCORE).
        self.min_score = min_score if min_score is not None else settings.VIABILITY_CRITIC_MIN_SCORE

    def critique(self, concept: dict) -> dict:
        """
        Args:
            concept: dict with at least product_name, product_format,
                     description, target_audience, buyer_reason (whatever
                     fields the caller has available — pass everything).

        Returns:
            {"passed": bool, "score": int (1-10), "reason": str}
            reason is always populated: on pass it's a brief note on what
            makes it work, on fail it's specific and actionable (usable as
            retry feedback).
        """
        prompt = f"""
You are an experienced Etsy seller and buyer with no stake in this
product. Your job is to score, honestly, how likely a real stranger
browsing Etsy would be to actually buy this specific item. You are NOT
checking format rules or schema; assume those are handled elsewhere.

Product concept:
{json.dumps(concept, indent=2)}

IMPORTANT — calibrate correctly. You are catching genuinely weak
products, not demanding perfection. Almost every product that sells on
Etsy sits in a crowded category next to dozens of similar listings —
"someone else already sells something like this" is NOT a reason to
reject. A concept description is deliberately brief; judge the IDEA's
commercial viability, not whether the two-sentence brief is a full
marketing pitch. Do NOT reject a solid, sellable concept just because you
can imagine an even fancier version — "could be more differentiated" is
almost always true and is not, by itself, disqualifying.

Score 1-10 on whether a real, specific type of person would plausibly pay
for THIS item, judging against these principles:

1. SPECIFICITY & CRAFT: Does it describe something with real
   visual/creative substance — a distinct illustration, layout, theme, or
   design idea — or is it just a generic label, a bare phrase, or a topic
   restated as if that were the product? "A poster about motivation" is a
   1-2; "a hand-lettered constellation map with the quote woven into the
   star pattern" is a strong score. A clearly-themed, specific concept
   (e.g. "cottagecore mushroom village coloring page", "houseplant
   watering + fertilizing planner") HAS real substance — score it well.

2. WOULD A REAL PERSON PAY: Picture a specific type of person scrolling
   Etsy. Would THEY plausibly buy this? Sharp appeal to a real, specific
   niche is good even if the niche is small. Only vague appeal to
   "everyone" with no hook is bad.

3. FORMAT-MARKET FIT: Does it match how this format actually sells?
   Coloring pages sell on charm/whimsy/aesthetic, NOT on teaching a skill
   or explaining a topic (a "gardening tips coloring page" is a poor fit).
   Planners sell on solving a real recurring task well. Wall art sells on
   a visual/emotional hook, not bare text. Judge the ACTUAL concept
   against what's true of that format's market.

4. EFFORT SIGNAL: Would producing this require real creative work, or
   could the whole thing be one line of text on a stock background?
   Low-effort text-on-blank products are weak.

Scoring guide (be consistent with this):
  1-3  = generic, lazy, a bare phrase/topic, or clear format mismatch.
         Would erode trust in the shop. REJECT.
  4-5  = a real idea but bland, forgettable, or a weak format fit;
         borderline, lean toward rejecting.
  6-7  = a solid, specific, sellable concept with a clear buyer and a
         real creative hook, even if similar things exist. ACCEPT.
  8-10 = distinctive and clearly compelling. ACCEPT.

Return ONLY valid JSON with this structure:
{{
  "score": <integer 1-10, honest commercial-viability score per the guide above>,
  "reason": "1-3 sentences. If the score is low, be specific and
             actionable about what's wrong and what change would fix it —
             this is fed back to the concept generator as retry guidance."
}}
"""
        response = self._generate(prompt)
        try:
            data = json.loads(response)
        except Exception:
            try:
                data = self.sanitizer.extract(response)
            except Exception as e:
                # Fail closed: if we can't parse the critique, treat it as
                # a rejection rather than silently letting the concept
                # through unreviewed.
                return {
                    "passed": False,
                    "score": 0,
                    "reason": f"Critic response could not be parsed ({e}); treating as rejected.",
                }

        score = int(data.get("score", 0)) if str(data.get("score", "")).strip().lstrip("-").isdigit() else 0
        passed = score >= self.min_score
        return {
            "passed": passed,
            "score": score,
            "reason": data.get("reason", "No reason given."),
        }

    def run(self, task: dict) -> dict:
        concept = task.get("concept", {})
        return self.critique(concept)
