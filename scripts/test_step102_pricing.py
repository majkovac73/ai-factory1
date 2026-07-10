"""
Step 102 / P0-11 test — per-format price bands + clamp; never send 0.

  [1] clamp_price returns the value when in-band.
  [2] clamp_price returns the band MIDPOINT for None / 0 / out-of-range / bool.
  [3] every format has a sane band (low>0, low<high).
  [4] EtsyClient.create_draft_listing raises on an invalid (0/None) price
      instead of silently sending 0.

Usage: python scripts/test_step102_pricing.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.product_formats import PRODUCT_FORMATS, price_band_for, clamp_price
from app.services.etsy_client import EtsyClient

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] in-band values preserved
check("1 coloring_page 3.00 kept", clamp_price(3.00, "coloring_page") == 3.00)
check("1 pdf 9.99 kept", clamp_price(9.99, "pdf_planner_or_guide") == 9.99)

# [2] invalid -> midpoint
lo, hi = price_band_for("coloring_page")
mid = round((lo + hi) / 2, 2)
check("2 None -> midpoint", clamp_price(None, "coloring_page") == mid)
check("2 0 -> midpoint", clamp_price(0, "coloring_page") == mid)
check("2 above band -> midpoint", clamp_price(999, "coloring_page") == mid)
check("2 below band -> midpoint", clamp_price(0.01, "coloring_page") == mid)
check("2 bool True not treated as 1", clamp_price(True, "coloring_page") == mid)

# [3] all bands sane
all_sane = True
for fmt, spec in PRODUCT_FORMATS.items():
    blo, bhi = price_band_for(fmt)
    if not (blo > 0 and blo < bhi):
        all_sane = False
        print(f"    band problem: {fmt} -> {(blo, bhi)}")
check("3 all formats have sane bands", all_sane)
check("3 coloring_page cheaper than pdf", price_band_for("coloring_page")[1] < price_band_for("pdf_planner_or_guide")[1])

# [4] EtsyClient refuses invalid price
raised = False
try:
    asyncio.run(EtsyClient().create_draft_listing({"title": "x", "price": 0}))
except ValueError:
    raised = True
except Exception:
    # any other exception means it got PAST the price guard (bad) then failed elsewhere
    raised = False
check("4 create_draft_listing raises ValueError on price=0", raised)

raised_none = False
try:
    asyncio.run(EtsyClient().create_draft_listing({"title": "x"}))
except ValueError:
    raised_none = True
except Exception:
    raised_none = False
check("4 create_draft_listing raises ValueError on missing price", raised_none)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 pricing tests passed.")
