"""
EtsyMarketService (STEP 103 A-2) — validate concepts against REAL Etsy buyer-side
data, not just Google search interest.

Google Trends measures what people google, not what they buy on Etsy. Etsy's
public findAllListingsActive endpoint (api-key only, NO OAuth) returns the live
competing-listing count for a keyword plus the top results' real prices and
titles — i.e. how saturated the niche is, what winning sellers charge, and how
they write titles. A concept hot on Google can be a graveyard on Etsy (100k+
competitors) or priced far from our band.

Everything degrades gracefully: any failure returns None and the pipeline
proceeds without market grounding (never blocks on missing market data).
"""
import logging

import httpx

from config import settings

logger = logging.getLogger("ai-factory")

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


class EtsyMarketService:
    def __init__(self, api_key: str = None):
        # Public endpoint: the x-api-key is the app keystring only (no OAuth).
        self._api_key = api_key or settings.ETSY_API_KEY

    async def validate_concept(self, keywords: str, limit: int = 100) -> dict | None:
        """Return {competition_count, price_p25, price_p50, price_p75, currency,
        top_titles[:10]} for a keyword phrase, or None if unavailable."""
        if not self._api_key or not keywords:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{ETSY_API_BASE}/listings/active",
                    headers={"x-api-key": self._api_key},
                    params={"keywords": keywords, "limit": limit, "sort_on": "score"},
                )
            if resp.status_code >= 400:
                logger.warning(f"EtsyMarketService: {resp.status_code} for '{keywords}': {resp.text[:150]}")
                return None
            data = resp.json()
        except Exception as e:
            logger.warning(f"EtsyMarketService: market lookup failed for '{keywords}': {e}")
            return None

        return self._summarize(data)

    @staticmethod
    def _summarize(data: dict) -> dict:
        results = data.get("results", []) or []
        count = int(data.get("count", len(results)) or 0)

        prices = []
        currency = "USD"
        for r in results:
            price = r.get("price") or {}
            amt, div = price.get("amount"), price.get("divisor") or 100
            if amt is not None:
                prices.append(amt / div)
                currency = price.get("currency_code", currency)
        prices.sort()

        def pct(p):
            if not prices:
                return None
            idx = min(len(prices) - 1, int(round(p * (len(prices) - 1))))
            return round(prices[idx], 2)

        top_titles = [r.get("title", "") for r in results[:10] if r.get("title")]

        return {
            "competition_count": count,
            "price_p25": pct(0.25),
            "price_p50": pct(0.50),
            "price_p75": pct(0.75),
            "currency": currency,
            "top_titles": top_titles,
        }
