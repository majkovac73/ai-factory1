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

from config import settings

logger = logging.getLogger("ai-factory")

# Etsy-relevant seed categories. Edit this list over time as you learn what
# converts — this is not meant to be exhaustive, just a real anchor point.
# Can be overridden without a code change via settings.TREND_SEED_KEYWORDS.
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

    def _default_keywords(self) -> list:
        configured = getattr(settings, "TREND_SEED_KEYWORDS", None) or []
        return list(configured) if configured else SEED_KEYWORDS

    def get_real_trend_signals(self, keywords: "list[str] | None" = None) -> dict:
        """
        Returns real Google Trends data for the given keywords (or the
        configured/default seed keywords):
          {
            "keywords": [...],
            "rising_queries": {keyword: [rising_query, ...], ...},
            "interest_snapshot": {keyword: latest_interest_value, ...},
          }

        Raises TrendDataFetchError if no real data could be retrieved after
        retries. Does not return partial fake data — either it's real or
        it's an exception.
        """
        kws = keywords or self._default_keywords()
        rising_queries: dict = {}
        interest_snapshot: dict = {}
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
