"""
Step 104-D test — trend direction (1-5) + stale-cache fallback & alert (1-6).

  1-5: _series_direction marks a rising vs falling synthetic series correctly,
       dropping the final partial bucket.
  1-6: fetch raises + cache 3 days old -> stale payload served (stale=True);
       fetch raises + cache 10 days old -> raises + alert.

Usage: python scripts/test_step104_trend_direction_stale.py
"""
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

os.environ["IMAGE_STORAGE_ROOT"] = os.path.join(tempfile.mkdtemp(), "images")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from app.services.trend_data_service import TrendDataService, TrendDataFetchError

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


svc = TrendDataService.__new__(TrendDataService)  # skip pytrends init

# 1-5: direction
rising = pd.DataFrame({
    "kw": [10, 12, 11, 13, 30, 34, 36, 40, 5],
    "isPartial": [False] * 8 + [True],   # last bucket partial -> dropped
})
now, prev, direction = svc._series_direction(rising, "kw")
check("1-5 rising series marked rising", direction == "rising")
check("1-5 partial last bucket dropped (now not dragged to ~5)", now > prev)

falling = pd.DataFrame({
    "kw": [40, 38, 36, 34, 12, 10, 9, 8, 3],
    "isPartial": [False] * 8 + [True],
})
_, _, d2 = svc._series_direction(falling, "kw")
check("1-5 falling series marked falling", d2 == "falling")

flat = pd.DataFrame({"kw": [20, 21, 19, 20, 21, 20, 19, 21], "isPartial": [False] * 8})
_, _, d3 = svc._series_direction(flat, "kw")
check("1-5 flat series marked flat", d3 == "flat")

# 1-6: stale fallback
kws = ["printable wall art"]
payload = {"keywords": kws, "rising_queries": {}, "interest_snapshot": {"printable wall art": 50}}


def write_cache(age_days):
    p = svc._cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"_cached_at": time.time() - age_days * 86400, "payload": payload}), encoding="utf-8")


# 3-day-old cache + fetch fails -> stale served
write_cache(3)
with patch.object(TrendDataService, "_default_keywords", return_value=kws), \
     patch.object(TrendDataService, "_load_cache", return_value=None), \
     patch.object(TrendDataService, "_fetch_live", side_effect=TrendDataFetchError("429 ban")), \
     patch.object(TrendDataService, "_alert_stale") as alert_stale:
    out = svc.get_real_trend_signals()
check("1-6 3-day-old cache served on failure", out.get("stale") is True)
check("1-6 stale payload keeps data", out.get("interest_snapshot", {}).get("printable wall art") == 50)
check("1-6 stale alert fired", alert_stale.called)

# 10-day-old cache + fetch fails -> raises + ban alert
write_cache(10)
raised = False
with patch.object(TrendDataService, "_default_keywords", return_value=kws), \
     patch.object(TrendDataService, "_load_cache", return_value=None), \
     patch.object(TrendDataService, "_fetch_live", side_effect=TrendDataFetchError("429 ban")), \
     patch.object(TrendDataService, "_alert_ban_once_per_day") as ban_alert:
    try:
        svc.get_real_trend_signals()
    except TrendDataFetchError:
        raised = True
check("1-6 cache too old -> raises", raised)
check("1-6 ban alert fired", ban_alert.called)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-D trend-direction/stale tests passed.")
