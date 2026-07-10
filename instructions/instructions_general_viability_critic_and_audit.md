# Instructions: General product-viability critic + retroactive listing audit

## Context (read first)

Repo: `ai-factory1`. This follows `instructions_real_trend_data.md` and
`instructions_sellability_and_brand_safety.md`.

That second file added keyword-based checks (reject coloring pages
mentioning "math"/"gardening tips", reject single_print concepts matching
"quote on plain background", etc.). **That approach is too narrow.** It
only catches the exact failure patterns already observed in the shop —
it will do nothing against the next low-value idea that doesn't happen to
contain one of those specific words. A hardcoded keyword list is a patch,
not a fix.

This document replaces that narrow approach with something durable: a
genuine judgment step — a second LLM call whose only job is to critique
"would a real stranger actually buy this," reasoning from principles, not
string matching. It also adds a retroactive audit so existing weak
listings already in the shop get caught too, not just future ones.

Two parts:
- **Part A** — a general `ProductViabilityCriticAgent` that replaces the
  keyword checks from the previous instructions.
- **Part B** — an audit script that runs the same critic against every
  currently-published Etsy listing and reports which ones don't clear the
  bar, so Maj can clean up the shop, not just stop new bad listings.

---

## Part A — General product-viability critic

### A1. New file: `app/agents/product_viability_critic.py`

```python
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


class ProductViabilityCriticAgent(BaseAgent):

    def __init__(self, provider=None, model: str = None):
        super().__init__(provider, model)
        self.sanitizer = JSONSanitizer()

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
product — your only job is to judge, honestly, whether a real stranger
browsing Etsy would actually pay for this specific item. You are not
checking format rules or schema; assume those are already handled
elsewhere. You are the last honest check before this becomes a real
listing that costs real money and effort to produce.

Product concept:
{json.dumps(concept, indent=2)}

Judge it against these principles — reason about THIS specific concept,
don't pattern-match to examples:

1. SPECIFICITY & CRAFT: Does the concept describe something with real
   visual/creative substance — a distinct illustration, layout, or design
   idea — or is it just a generic label, a bare phrase, or a topic
   restated as if that were the product? "A poster about motivation" is
   not a product; "a hand-lettered constellation map with the quote
   woven into the star pattern" is.

2. WOULD A STRANGER ACTUALLY PAY: Picture a real, specific type of
   person scrolling Etsy. Would THEY stop, and would THEY pay roughly
   EUR 12-25 for exactly this, today — not "someone, somewhere, might
   like this category." Vague appeal to a huge generic audience is a bad
   sign; sharp appeal to a real, specific person is a good sign.

3. FORMAT-MARKET FIT: Does the concept match how this specific format
   actually sells on Etsy, based on what you know of the category? (For
   example: coloring pages tend to sell on charm/whimsy/aesthetic
   appeal, not on teaching a skill or explaining a topic. Planners and
   trackers tend to sell on solving a real recurring task well, not just
   existing. Wall art sells on a strong visual/emotional hook, not text
   alone.) Judge the ACTUAL concept in front of you against what you
   know is true of that format's market — do not just check for known
   bad keywords.

4. DIFFERENTIATION: Is there anything here beyond "a generic item in
   this category"? What, if anything, makes this the one someone picks
   over the dozens of similar listings already on Etsy?

5. EFFORT SIGNAL: Would producing this actually require real creative
   work (composition, illustration, thoughtful layout), or could the
   entire thing be one line of text dropped onto a stock background?
   Low-effort-looking products erode trust in the whole shop even if one
   sells.

Be honest and a little skeptical — err toward rejecting anything you'd
personally scroll past. A rejection here is cheap; a bad listing that
goes live is not.

Return ONLY valid JSON with this structure:
{{
  "passed": true or false,
  "score": <integer 1-10, honest commercial-viability score>,
  "reason": "1-3 sentences. If failing, be specific and actionable about
             what's wrong and what kind of change would fix it — this
             will be fed back to the concept generator as retry
             guidance."
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

        return {
            "passed": bool(data.get("passed", False)),
            "score": int(data.get("score", 0)) if str(data.get("score", "")).strip().isdigit() else 0,
            "reason": data.get("reason", "No reason given."),
        }

    def run(self, task: dict) -> dict:
        concept = task.get("concept", {})
        return self.critique(concept)
```

### A2. Wire it into `TrendResearchAgent`

In `app/agents/trend_research_agent.py`:

1. Import it near the other imports:
   ```python
   from app.agents.product_viability_critic import ProductViabilityCriticAgent
   ```
2. Instantiate it in `__init__` alongside `self._research` / `self._intelligence`:
   ```python
   self._critic = ProductViabilityCriticAgent(provider, model)
   ```
3. In `_propose_product()`, after the existing `error = self._validate_product(data)` /
   `if not error:` block succeeds (i.e. the concept is schema-valid), add
   the critique as an ADDITIONAL gate before returning — do not return on
   schema validity alone anymore:

   ```python
            error = self._validate_product(data)
            if not error:
                data["confidence"] = data.get("confidence") or fallback_confidence

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
   ```

   This means a concept must now pass BOTH the schema/format validator AND
   the independent viability critic before a task is ever created — and a
   viability rejection consumes a retry attempt exactly like a schema
   rejection already does, feeding the critic's specific reason back to
   the generator.

### A3. Remove the narrow keyword checks from the previous instructions

If the keyword-based checks from `instructions_sellability_and_brand_safety.md`
Part A2 (`_DRY_TOPIC_MARKERS`, `_NO_VISUAL_HOOK_MARKERS`,
`_VAGUE_BUYER_REASON_MARKERS`) were already implemented, remove them —
the critic above supersedes them with a general judgment instead of a
list of known bad phrases. **Keep** the brand/IP check
(`_BRAND_IP_MARKERS`) from that same file — that one is a categorical
legal/risk rule, not a judgment call, and stays as-is alongside the new
critic.

The `buyer_reason` field from the previous instructions can stay in the
schema (it's useful context to feed the critic) but is no longer the
mechanism doing the rejecting — the critic is.

---

## Part B — Retroactive audit of already-published listings

The critic above only prevents *future* problems. Fixing the shop also
means reviewing what's *already live* — the screenshot Maj shared shows
several published listings that likely wouldn't pass this bar.

### B1. New script: `scripts/audit_existing_listings.py`

```python
"""
Audit existing published Etsy listings against ProductViabilityCriticAgent.

This is a DRY-RUN reporting tool by default — it does NOT deactivate or
delete anything on its own. It prints/saves a report so Maj can review
and decide what to do with each flagged listing. Use --deactivate only
after reviewing the report, and even then it asks for explicit
confirmation per listing (see below) — never bulk-deletes automatically.

Usage:
  python scripts/audit_existing_listings.py                  # report only
  python scripts/audit_existing_listings.py --deactivate      # also offers
                                                               # to deactivate
                                                               # flagged listings,
                                                               # one at a time,
                                                               # with confirmation
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.product_viability_critic import ProductViabilityCriticAgent
from app.integrations.etsy_client import EtsyClient  # adjust import path to match actual location


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deactivate", action="store_true")
    args = parser.parse_args()

    etsy = EtsyClient()
    critic = ProductViabilityCriticAgent()

    # Adjust to whatever the real EtsyClient method is for listing all
    # active listings for shop 58716525 — check EtsyClient for the exact
    # method name/signature before running.
    listings = etsy.get_all_active_listings(shop_id=58716525)

    results = []
    for listing in listings:
        concept = {
            "product_name": listing.get("title"),
            "description": listing.get("description", "")[:600],
            "product_format": listing.get("taxonomy_id"),  # best available proxy; note in report
            "target_audience": "",
            "buyer_reason": "",
        }
        critique = critic.critique(concept)
        results.append({
            "listing_id": listing.get("listing_id"),
            "title": listing.get("title"),
            "url": listing.get("url"),
            "passed": critique["passed"],
            "score": critique["score"],
            "reason": critique["reason"],
        })
        print(
            f"[{'PASS' if critique['passed'] else 'FLAG'}] "
            f"score={critique['score']} '{listing.get('title')}' — {critique['reason']}"
        )

    flagged = [r for r in results if not r["passed"]]
    report_path = "audit_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{len(flagged)} of {len(results)} listings flagged. Full report: {report_path}")

    if args.deactivate and flagged:
        for r in flagged:
            answer = input(
                f"\nDeactivate listing {r['listing_id']} \"{r['title']}\" "
                f"(score={r['score']}, reason: {r['reason']})? [y/N] "
            )
            if answer.strip().lower() == "y":
                etsy.deactivate_listing(r["listing_id"])  # verify real method name first
                print(f"Deactivated {r['listing_id']}")
            else:
                print("Skipped.")


if __name__ == "__main__":
    main()
```

Before running: check the real `EtsyClient` class for the actual method
names to list active listings and deactivate a listing — don't guess the
API shape, verify against the live client code and Etsy's v3 docs, same
as every other Etsy integration in this project.

### B2. Run it

```
railway ssh
python scripts/audit_existing_listings.py
```
Review `audit_report.json` and the printed output with Maj before running
with `--deactivate`. Nothing should be removed from the live shop without
a human seeing the specific reason first.

---

## Testing

Before wiring the critic into the live pipeline:
1. Unit test `ProductViabilityCriticAgent.critique()` with `_generate`
   mocked to return a few canned critiques (one pass, one fail, one
   unparseable-response case) and confirm the parsing/fail-closed
   behavior works.
2. Unit test `TrendResearchAgent._propose_product()` with the critic
   mocked to fail once then pass, confirming a rejected-by-critique
   concept consumes a retry attempt and the critic's reason is passed
   into the next `_build_concept_prompt()` call's feedback.
3. Do NOT skip a live (non-mocked) check: run the critic against 5-10
   real concept examples — mix some you'd expect to pass (a specific,
   well-thought-out planner) and some you'd expect to fail (a generic
   phrase-on-background poster) — and manually confirm the critic's
   judgment matches your own read, not just that it returns valid JSON.

## Deploy and verify — don't stop until this is real

Same standing rule as the prior two documents: keep iterating — read
actual errors, fix, rerun — until all of this is true, with pasted
evidence:

- [ ] Local mocked tests for the critic and its wiring into
      `TrendResearchAgent` pass
- [ ] A handful of live, non-mocked critique calls were manually reviewed
      and the judgments look sound to a human, not just schema-valid
- [ ] `railway ssh` manual runs of `TrendResearchAgent().run()` in
      production (still with `AUTONOMY_ENABLED=false`) show concepts that
      are actually being rejected and retried when weak, with real
      critic reasons in the logs
- [ ] `scripts/audit_existing_listings.py` has been run against the real
      shop, produced a real `audit_report.json`, and Maj has reviewed it
      (deactivation decisions are Maj's call, not something to automate
      away)

If the critic itself seems to be judging inconsistently or too
leniently/harshly across your manual spot-checks, that's a prompt-quality
problem worth fixing before moving on — don't ship a critic that doesn't
actually match a human's judgment.
