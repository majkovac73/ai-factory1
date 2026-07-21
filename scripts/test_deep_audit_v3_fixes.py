"""
DEEP AUDIT V3 fixes: EUR currency normalization + backoff on all clients.

Usage: python scripts/test_deep_audit_v3_fixes.py
"""
import os, sys, tempfile, asyncio
from unittest.mock import patch

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "v3.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

# ── currency ──────────────────────────────────────────────────────────────
from app.core.currency import usd_to_base, base_currency
with patch.object(settings, "BASE_CURRENCY", "EUR"), patch.object(settings, "USD_TO_BASE_RATE", 0.92):
    check("base currency is EUR", base_currency() == "EUR")
    check("usd_to_base converts $10 -> 9.2 EUR", abs(usd_to_base(10) - 9.2) < 1e-6)
    check("usd_to_base handles None -> 0", usd_to_base(None) == 0.0)

# pnl_by_listing converts cost USD->EUR; revenue stays EUR; net is single-currency
from app.db.database import Base, engine
from app.core import cost_context
from app.services.revenue_service import RevenueService
Base.metadata.create_all(bind=engine)
with patch.object(settings, "BASE_CURRENCY", "EUR"), patch.object(settings, "USD_TO_BASE_RATE", 0.92):
    with cost_context.cost_attribution("t-eur"):
        cost_context.record_cost(1.00, use_case="image")   # $1.00 USD cost
    rs = RevenueService()
    rs.record_sale(task_id="t-eur", amount=10.00, currency="EUR", transaction_id="txE")  # EUR revenue
    rows = rs.pnl_by_listing()
    row = next((r for r in rows if r["task_id"] == "t-eur"), None)
    check("pnl row currency is EUR", row and row["currency"] == "EUR")
    check("pnl cost converted USD->EUR ($1 -> 0.92 EUR)", row and abs(row["cost"] - 0.92) < 1e-6)
    check("pnl net = EUR revenue - EUR cost (10 - 0.92)", row and abs(row["net"] - (10.0 - 0.92)) < 1e-3)

# POD price uses EUR-converted production cost
from app.services.pod_fulfillment_service import PODFulfillmentService
with patch.object(settings, "BASE_CURRENCY", "EUR"), patch.object(settings, "USD_TO_BASE_RATE", 0.92), \
     patch.object(settings, "POD_SHIPPING_ESTIMATE_USD", 5.0), patch.object(settings, "POD_TARGET_PROFIT_USD", 6.0), \
     patch.object(settings, "POD_ETSY_FEE_FRACTION", 0.12):
    # cost 2000 cents = $20 USD -> 18.4 EUR; price = ceil((18.4+5+0.2+6)/0.88) -> ceil(33.6/0.88=33.6..)= ceil(33.86)=34 -> 3400
    price = PODFulfillmentService._pod_price_cents_from_cost(2000)
    # sanity: price must at least cover the EUR-converted cost + fees
    check("POD price covers EUR cost + margin", price >= 3300 and price % 100 == 0)

# ── backoff on all clients (sync + async) ───────────────────────────────────
from app.core import http_backoff
from app.core.http_backoff import request_with_backoff_sync, request_with_backoff

class _R:
    def __init__(s, code): s.status_code = code; s.headers = {"Retry-After": "0"}; s.reason_phrase = ""
class _SyncClient:
    def __init__(s, seq): s.seq = list(seq); s.calls = 0
    def _c(s): s.calls += 1; return _R(s.seq.pop(0) if s.seq else 200)
    def get(s, u, **k): return s._c()
    def post(s, u, **k): return s._c()

with patch.object(http_backoff.time, "sleep", lambda *_: None):
    c = _SyncClient([429, 200]); r = request_with_backoff_sync(c, "GET", "u", max_retries=3)
    check("sync GET retried on 429", c.calls == 2 and r.status_code == 200)
    c2 = _SyncClient([500, 200]); r2 = request_with_backoff_sync(c2, "POST", "u", max_retries=3)
    check("sync POST NOT retried on 500 (no double-submit)", c2.calls == 1 and r2.status_code == 500)
    c3 = _SyncClient([429, 200]); r3 = request_with_backoff_sync(c3, "POST", "u", max_retries=3)
    check("sync POST retried on 429", c3.calls == 2 and r3.status_code == 200)

# the three clients now import the backoff wrappers (wired in)
import app.services.printify_client as pc
import app.marketing.tumblr_channel as tc
import app.marketing.pinterest_channel as pinc
check("printify_client uses backoff", "request_with_backoff_sync" in open(pc.__file__).read())
check("tumblr_channel uses backoff", "request_with_backoff" in open(tc.__file__).read())
check("pinterest_channel uses backoff", "request_with_backoff" in open(pinc.__file__).read())

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All DEEP AUDIT V3 fix tests passed.")
