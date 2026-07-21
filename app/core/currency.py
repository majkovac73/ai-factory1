"""
Currency normalization (DEEP AUDIT V3).

The Etsy shop lists and sells in EUR (BASE_CURRENCY), but provider costs
(OpenRouter image/LLM spend, Printify production) are billed in USD. Reporting
EUR revenue minus USD costs as if they were the same unit overstates/understates
profit by the FX gap (~8% at EUR/USD ~= 1.08) and mixes units. These helpers
convert USD amounts into the shop's base currency so the P&L is internally
consistent. The rate is a configurable ESTIMATE (FX drifts) — not a live feed.
"""
from config import settings


def base_currency() -> str:
    return getattr(settings, "BASE_CURRENCY", "EUR")


def usd_to_base(usd) -> float:
    """Convert a USD amount into the shop's base currency (EUR)."""
    try:
        rate = float(getattr(settings, "USD_TO_BASE_RATE", 0.92))
        return round(float(usd or 0.0) * rate, 4)
    except Exception:
        return float(usd or 0.0)
