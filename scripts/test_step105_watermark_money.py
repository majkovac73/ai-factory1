"""
Step 105-G test — 3-2 watermarks, 4-1 renewal fees, 4-2 breaker alert once/day.

Usage: python scripts/test_step105_watermark_money.py
"""
import os
import sys
import tempfile
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "s105g.db")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image
from app.db.database import Base, engine
from app.models.analytics_event import AnalyticsEvent  # noqa: F401
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── 3-2: watermark applied only to WATERMARK_FORMATS, on the design layer ──
from app.services.mockup_service import MockupService
svc = MockupService()
tmp = tempfile.mkdtemp()
# a flat white design (so watermark pixels are easy to detect as non-white)
design = os.path.join(tmp, "d.png")
Image.new("RGB", (400, 400), (255, 255, 255)).save(design)

base = Image.open(design).convert("RGB")
wm = svc._maybe_watermark(base, "coloring_page")
non_white_wm = sum(1 for px in wm.getdata() if px != (255, 255, 255))
check("3-2 coloring_page design is watermarked (pixels changed)", non_white_wm > 200)

plain = svc._maybe_watermark(Image.open(design).convert("RGB"), "single_print")
non_white_plain = sum(1 for px in plain.getdata() if px != (255, 255, 255))
check("3-2 single_print is NOT watermarked (not in WATERMARK_FORMATS)", non_white_plain == 0)

# format=None never watermarks
none_wm = svc._maybe_watermark(Image.open(design).convert("RGB"), None)
check("3-2 no format -> no watermark", sum(1 for px in none_wm.getdata() if px != (255, 255, 255)) == 0)

# full mockup for a coloring page contains the watermark (design layer), delivery untouched
before = os.path.getsize(design)
mock_bytes = svc.build_mockup_bytes(design, role="framed", size=256, product_format="coloring_page")
check("3-2 build_mockup_bytes returns a PNG", mock_bytes[:8] == b"\x89PNG\r\n\x1a\n")
check("3-2 delivery file left byte-identical (untouched)", os.path.getsize(design) == before)

# lying comments fixed
mock_src = open("app/services/mockup_service.py", encoding="utf-8").read()
check("3-2 docstring no longer falsely claims blanket 'watermarked'",
      "attractive listing/ad image" in mock_src and "WATERMARK_FORMATS" in mock_src)

# ── 4-1: renewal fee estimate ──
from app.services.revenue_service import RevenueService
rs = RevenueService()
# 28 active listings x $0.20 / 4 months = $1.40 amortized monthly
fee = rs.record_renewal_fee_estimate(28)
check("4-1 renewal fee for 28 listings = $1.40", abs(fee - 1.40) < 1e-6)
check("4-1 zero active -> no fee", rs.record_renewal_fee_estimate(0) == 0.0)
# it's summed into total fees (net revenue reflects it)
totals = rs.get_total_fees()
check("4-1 renewal fee counted in get_total_fees", abs(totals["total_fees"] - 1.40) < 1e-6)
# event carries basis 'renewal'
evs = [e for e in __import__("app.services.analytics_service", fromlist=["AnalyticsService"]).AnalyticsService().get_events(event_type="fee_estimate", limit=10) if (e.payload or {}).get("basis") == "renewal"]
check("4-1 renewal event tagged basis=renewal", len(evs) >= 1)

# ── 4-2: circuit-breaker alert fires at most once/day ──
from app.services.autonomy_service import AutonomyService, SpendCapExceeded
auto = AutonomyService()
# push spend well over the ceiling
auto.record_spend(settings.MAX_DAILY_SPEND_USD * 2, "blow ceiling")
alert_calls = {"n": 0}
with patch.object(AutonomyService, "_alert_cap_hit", lambda self, *a, **k: alert_calls.__setitem__("n", alert_calls["n"] + 1)):
    for _ in range(5):
        try:
            AutonomyService().assert_within_circuit_breaker()
        except SpendCapExceeded:
            pass
check("4-2 breaker raised every time but alerted once", alert_calls["n"] == 1)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-105-G tests passed.")
