"""
Step 103 / A-2 test — Etsy market validation.

  [1] EtsyMarketService._summarize computes competition count, price
      percentiles, and top titles from a findAllListingsActive-shaped payload.
  [2] validate_concept returns the summary for a stubbed HTTP response, and
      None (graceful) on error / missing key.
  [3] _attach_market attaches market data onto the concept dict.
  [4] pricing grounding: market median inside the band is used; outside it,
      the clamp band applies (mirrors the orchestrator logic).

Usage: python scripts/test_step103_market.py
"""
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.etsy_market_service import EtsyMarketService
from app.core.product_formats import price_band_for, clamp_price
from config import settings

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


payload = {
    "count": 45210,
    "results": [
        {"title": "Boho Wall Art Print", "price": {"amount": 500, "divisor": 100, "currency_code": "USD"}},
        {"title": "Sage Green Print", "price": {"amount": 400, "divisor": 100, "currency_code": "USD"}},
        {"title": "Minimalist Line Art", "price": {"amount": 600, "divisor": 100, "currency_code": "USD"}},
        {"title": "Desert Sunset Print", "price": {"amount": 300, "divisor": 100, "currency_code": "USD"}},
    ],
}

# [1] summarize
s = EtsyMarketService._summarize(payload)
check("1 competition count", s["competition_count"] == 45210)
check("1 median price computed", s["price_p50"] in (4.0, 5.0))  # p50 index of 4 sorted [3,4,5,6]
check("1 p25 <= p50 <= p75", s["price_p25"] <= s["price_p50"] <= s["price_p75"])
check("1 top titles present", "Boho Wall Art Print" in s["top_titles"])

# [2] validate_concept over stubbed HTTP
class _Resp:
    status_code = 200
    text = ""
    def json(self): return payload

class _Client:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return _Resp()

with patch.object(settings, "ETSY_API_KEY", "key"), \
     patch("app.services.etsy_market_service.httpx.AsyncClient", _Client):
    res = asyncio.run(EtsyMarketService().validate_concept("boho wall art"))
check("2 validate_concept returns summary", res and res["competition_count"] == 45210)

# no key -> None
with patch.object(settings, "ETSY_API_KEY", None):
    res_none = asyncio.run(EtsyMarketService(api_key=None).validate_concept("x"))
check("2 no api key -> None (graceful)", res_none is None)

# error -> None
class _ErrClient(_Client):
    async def get(self, *a, **k):
        raise RuntimeError("boom")
with patch.object(settings, "ETSY_API_KEY", "key"), \
     patch("app.services.etsy_market_service.httpx.AsyncClient", _ErrClient):
    res_err = asyncio.run(EtsyMarketService().validate_concept("x"))
check("2 network error -> None (graceful)", res_err is None)

# [3] _attach_market
with patch("app.agents.base_agent.ProviderManager.get_provider", return_value=object()):
    from app.agents.trend_research_agent import TrendResearchAgent
    agent = TrendResearchAgent()
data = {"product_name": "Boho Wall Art Print", "product_format": "single_print"}
with patch("app.services.etsy_market_service.EtsyMarketService.validate_concept",
           new=AsyncMock(return_value=s)):
    agent._attach_market(data)
check("3 market attached to concept dict", data.get("market", {}).get("competition_count") == 45210)

# [4] pricing grounding logic (mirror of orchestrator)
lo, hi = price_band_for("single_print")   # (3.50, 8.00)
def ground(market_p):
    price = clamp_price(None, "single_print")  # band midpoint
    if market_p and lo <= market_p <= hi:
        price = round(market_p, 2)
    return price
check("4 in-band market median used", ground(5.0) == 5.0)
check("4 out-of-band market median ignored (band midpoint)", ground(50.0) == round((lo + hi) / 2, 2))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 market tests passed.")
