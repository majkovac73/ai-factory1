# Instructions: Replace guessed trend research with a real trend data source

## Context (read first, do not skip)

Repo: `ai-factory1`. FastAPI + SQLAlchemy + SQLite, deployed on Railway. This
repo is the AI Factory Etsy shop automation platform. You do not have access
to the full codebase history or prior chat context — everything you need is
below. Read the actual current file contents before editing anything; some
line numbers/exact text may have drifted since this was written.

**The problem:** `TrendResearchAgent` (in `app/agents/trend_research_agent.py`)
currently discovers "trending products" by asking an LLM to imagine what's
popular on Etsy from its training data. `ResearchAgent.research()`
(`app/agents/market_intelligence/research.py`) has no real data feed at
all — it's a single prompt asking the model to hallucinate market findings.
There is no live signal anywhere in this pipeline: no Etsy search data, no
Google Trends, no Pinterest, nothing. This is guessing dressed up as
"research."

**The fix:** Add a real, live trend data source and force the LLM pipeline
to reason over that real data instead of its own imagination. If the real
data fetch fails, the cycle must abort loudly — it must NEVER silently fall
back to pure-LLM guessing, because that would recreate the exact problem
being fixed.

**Chosen data source (assumption — flag to Maj if you disagree):** Google
Trends via the `pytrends` library. Reasoning: it requires no API key, no
app approval process (unlike Pinterest, which is still pending, and Tumblr,
which is mid-OAuth), and gives real search-interest data for candidate
Etsy-relevant categories (rising queries, interest-over-time). It is an
unofficial library that scrapes Google Trends' public frontend, so it can
occasionally rate-limit or break — build in retries and fail loudly rather
than silently, per above.

---

## Step 1 — Add the dependency

In `requirements.txt`, add:
```
pytrends==4.9.2
```

## Step 2 — New file: `app/services/trend_data_service.py`

Create this new service. It is the only place that talks to Google Trends.

```python
"""
TrendDataService — real market signal for TrendResearchAgent.

Replaces LLM-imagined trend data with actual Google Trends data (via the
unofficial pytrends client) for a fixed set of Etsy-relevant seed
categories. This is an unofficial API that scrapes Google Trends' public
frontend — it can rate-limit or fail. On failure this service raises;
callers must NOT silently fall back to guessing.
"""
import logging
import time
import random

from pytrends.request import TrendReq

logger = logging.getLogger("ai-factory")

# Etsy-relevant seed categories. Edit this list over time as you learn what
# converts — this is not meant to be exhaustive, just a real anchor point.
SEED_KEYWORDS = [
    "digital planner",
    "printable wall art",
    "svg files",
    "sticker sheet",
    "wedding invitation template",
    "printable planner",
    "clipart bundle",
    "birthday party printable",
]

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 5


class TrendDataFetchError(Exception):
    """Raised when real trend data cannot be obtained. Callers must treat
    this as a hard stop for the cycle, not a signal to fall back to
    LLM-guessed data."""
    pass


class TrendDataService:
    def __init__(self):
        self._pytrends = TrendReq(hl="en-US", tz=360)

    def get_real_trend_signals(self, keywords: list[str] | None = None) -> dict:
        """
        Returns real Google Trends data for the given keywords (or
        SEED_KEYWORDS by default):
          {
            "keywords": [...],
            "rising_queries": {keyword: [rising_query, ...], ...},
            "interest_snapshot": {keyword: latest_interest_value, ...},
          }

        Raises TrendDataFetchError if no real data could be retrieved after
        retries. Does not return partial fake data — either it's real or
        it's an exception.
        """
        kws = keywords or SEED_KEYWORDS
        rising_queries: dict[str, list[str]] = {}
        interest_snapshot: dict[str, int] = {}
        last_error = None

        for kw in kws:
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    self._pytrends.build_payload([kw], timeframe="today 3-m")

                    interest_df = self._pytrends.interest_over_time()
                    if not interest_df.empty:
                        interest_snapshot[kw] = int(interest_df[kw].iloc[-1])

                    related = self._pytrends.related_queries()
                    rising = related.get(kw, {}).get("rising")
                    if rising is not None and not rising.empty:
                        rising_queries[kw] = rising["query"].head(5).tolist()

                    break  # success for this keyword, move to next
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"TrendDataService: attempt {attempt} failed for '{kw}': {e}"
                    )
                    if attempt < _MAX_RETRIES:
                        time.sleep(_BACKOFF_BASE_SECONDS * attempt + random.uniform(0, 2))

        if not rising_queries and not interest_snapshot:
            raise TrendDataFetchError(
                f"TrendDataService: could not retrieve any real trend data "
                f"for keywords={kws}. Last error: {last_error}"
            )

        return {
            "keywords": kws,
            "rising_queries": rising_queries,
            "interest_snapshot": interest_snapshot,
        }
```

## Step 3 — Modify `app/agents/market_intelligence/research.py`

Change `ResearchAgent.research()` to accept real trend data and force the
prompt to reason over it instead of inventing findings.

Find:
```python
    def research(self, topic: str, scope: str = "general") -> str:
        """
        Research a market topic and return findings.
        
        Args:
            topic: What to research (e.g., "Etsy planner market")
            scope: Research scope (e.g., "competitors", "trends", "pricing")
        
        Returns:
            Research findings as a string
        """

        prompt = f"""
You are a market research analyst.

Research the following topic and provide comprehensive findings.

Topic: {topic}
Scope: {scope}

Provide:
- Key findings
- Notable competitors or trends
- Market size estimates (if available)
- Growth trajectory
- Potential opportunities

Be factual and data-driven. If you don't know something, say so.
"""

        return self._generate(prompt)
```

Replace with:
```python
    def research(self, topic: str, scope: str = "general", real_trend_data: dict | None = None) -> str:
        """
        Research a market topic and return findings, grounded in real
        trend data rather than model imagination.

        Args:
            topic: What to research (e.g., "Etsy planner market")
            scope: Research scope (e.g., "competitors", "trends", "pricing")
            real_trend_data: Output of TrendDataService.get_real_trend_signals().
                REQUIRED for meaningful output — if omitted, the model is
                explicitly told it has no real data and must say so rather
                than invent findings.

        Returns:
            Research findings as a string
        """
        if real_trend_data:
            data_block = (
                "Real Google Trends data collected this cycle (rising "
                "search queries and recent interest levels):\n"
                f"{real_trend_data}"
            )
        else:
            data_block = (
                "NO real trend data was available this cycle. Do not "
                "invent or assume any market findings. State plainly that "
                "no real data was available."
            )

        prompt = f"""
You are a market research analyst.

Topic: {topic}
Scope: {scope}

{data_block}

Using ONLY the real data above (if present), provide:
- Key findings grounded specifically in the rising queries / interest
  levels shown above — reference the actual keywords and numbers
- Notable patterns across the keywords
- Which specific keywords show the strongest real signal
- Potential opportunities tied directly to specific rising queries

Do not state a finding unless it is directly traceable to the real data
provided. If the real data is thin or absent, say so explicitly instead of
filling the gap with assumptions.
"""

        return self._generate(prompt)
```

## Step 4 — Modify `app/agents/trend_research_agent.py`

Locate the `run()` method (the one that calls `self._research.research(...)`
then `self._intelligence.synthesize(...)` then `self._propose_product(...)`).
It currently looks like:

```python
        try:
            research = self._research.research(_RESEARCH_TOPIC, _RESEARCH_SCOPE)
        except Exception as e:
            logger.error(f"TrendResearchAgent: research step failed: {e}")
            return None
```

Replace the start of `run()` so it fetches real data FIRST and aborts the
cycle (returns `None`, does not proceed) if real data can't be obtained:

```python
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
```

Leave everything after this (the `intelligence.synthesize(...)` call and
`_propose_product(...)` call) unchanged — they already consume `research`
as a string, and it will now contain real-data-grounded findings instead of
invented ones.

**Important:** do not add any except-and-continue-with-guessing branch
anywhere in this chain. A failed real-data fetch must always result in
`run()` returning `None` for that cycle. `AutonomyWorker` already handles a
`None` opportunity by skipping the cycle — no changes needed there.

## Step 5 — Config (optional but recommended)

In `config/settings.py`, add a setting so the seed keyword list can be
tuned without a redeploy-requiring code change later:
```python
TREND_SEED_KEYWORDS: list[str] = []  # empty = use TrendDataService.SEED_KEYWORDS default
```
Wire it through if you want; not required for correctness, skip if it adds
risk under time pressure.

---

## Step 6 — Test locally before touching Railway

Create `scripts/test_trend_data_service.py`:
- Test 1 (mocked): mock `TrendReq` so `interest_over_time()` and
  `related_queries()` return canned dataframes; assert
  `get_real_trend_signals()` returns the expected structured dict.
- Test 2 (mocked failure): mock `TrendReq` to always raise; assert
  `TrendDataFetchError` is raised after `_MAX_RETRIES` attempts.
- Test 3 (mocked): call `ResearchAgent.research()` with a fake
  `real_trend_data` dict (mock `_generate`) and assert the prompt sent to
  `_generate` contains the actual keyword strings from the fake data (i.e.
  the data is really being injected into the prompt, not ignored).
- Test 4 (mocked): call `TrendResearchAgent.run()` with `TrendDataService`
  mocked to raise `TrendDataFetchError`; assert `run()` returns `None` and
  that `ResearchAgent.research` was never called.

Run it:
```
python scripts/test_trend_data_service.py
```
All four must pass before proceeding. Fix and re-run until they do — do
not move to the next step with failing tests.

Then run a **live, non-mocked** check locally (real network call to Google
Trends) to confirm `pytrends` actually works from your machine:
```
python -c "from app.services.trend_data_service import TrendDataService; import json; print(json.dumps(TrendDataService().get_real_trend_signals(), indent=2))"
```
Confirm the output contains real, non-empty `rising_queries` or
`interest_snapshot` data — not an empty dict, not an exception.

---

## Step 7 — Deploy and verify in production

1. Commit and push to trigger the Railway deploy for `ai-factory1`.
2. Confirm the deploy succeeded (check Railway build logs for the new
   `pytrends` dependency installing cleanly — this is a new package, watch
   for install failures).
3. Using `railway ssh` (NOT `railway run` — `railway run` executes locally
   on Windows and never reaches Railway's real environment, see
   `MIGRATION_NOTES.md`), run the same live check against the deployed
   environment:
   ```
   python -c "from app.services.trend_data_service import TrendDataService; import json; print(json.dumps(TrendDataService().get_real_trend_signals(), indent=2))"
   ```
   This confirms two separate things that can each independently fail:
   real data must actually flow, AND Railway's outbound IP must not be
   blocked/rate-limited by Google (this is a real risk with pytrends from
   data-center IPs — if it fails here but worked locally, that's the
   cause; retry with longer backoff, and if it's still blocked, report
   this specific finding rather than declaring success).
4. With `AUTONOMY_ENABLED` still `false` in production, manually invoke a
   full cycle end to end (e.g. via `railway ssh` running a small script
   that instantiates `TrendResearchAgent()` and calls `.run()`) and confirm:
   - It does NOT create a real task (don't flip the kill switch)
   - The returned product concept is traceable to specific real keywords
     from the trend data (log or print the trend data alongside the
     resulting concept so this is checkable)

## Step 8 — Do not stop until this is actually verified

This is the standing instruction for this task: keep iterating — read the
actual error, fix it, redeploy or rerun as needed, and try again — until
all of the following are true, with evidence (paste actual command output/
logs, not a description of what should happen):

- [ ] Local mocked test suite (4 tests) passes
- [ ] Local live `pytrends` call returns real, non-empty data
- [ ] Railway deploy succeeds with `pytrends` installed
- [ ] `railway ssh` live call returns real, non-empty data from Railway's
      network (not just locally)
- [ ] A full `TrendResearchAgent().run()` cycle in production returns a
      product concept, and the trend data logged alongside it shows the
      concept is actually grounded in specific real rising-query data
- [ ] A forced-failure check (temporarily break connectivity or mock
      pytrends to fail) confirms the cycle returns `None` and does NOT
      fall back to inventing data

Do not report this as done, and do not ask whether to continue, until
every box above is checked with real evidence. If something is blocked by
something outside your control (e.g. Google permanently blocking Railway's
IP range), stop and report that specific, concrete blocker — don't paper
over it by silently reverting to LLM guessing.
