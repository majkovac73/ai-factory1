"""
Step 104-F test — 4-1 real P&L with estimated Etsy fees.

Usage: python scripts/test_step104_pnl_fees.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "f.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.services.revenue_service import RevenueService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

rs = RevenueService()

# A $10 sale: fee = 10*0.065 + 10*0.03 + 0.25 = 0.65 + 0.30 + 0.25 = 1.20
rs.record_sale(task_id="t1", amount=10.0, transaction_id="tx1")
fee = rs.record_fee_estimate(task_id="t1", sale_amount=10.0, transaction_id="tx1")
check("4-1 fee for $10 sale is $1.20", abs(fee - 1.20) < 1e-6)

# A $5 sale: fee = 5*0.065 + 5*0.03 + 0.25 = 0.325 + 0.15 + 0.25 = 0.725
rs.record_sale(task_id="t2", amount=5.0, transaction_id="tx2")
rs.record_fee_estimate(task_id="t2", sale_amount=5.0, transaction_id="tx2")

totals = rs.get_total_fees()
check("4-1 total fees = 1.20 + 0.725 = 1.925", abs(totals["total_fees"] - 1.925) < 1e-6)
check("4-1 two fee events recorded", totals["fee_count"] == 2)

rev = rs.get_total_revenue()
gross = rev["total_revenue"]
net = round(gross - totals["total_fees"], 2)
check("4-1 gross is 15.00", abs(gross - 15.0) < 1e-6)
check("4-1 net is 13.07 (gross - fees)", net == 13.07)

# non-positive sale -> no fee
check("4-1 zero sale records no fee", rs.record_fee_estimate("t3", 0.0) == 0.0)

# ── /dashboard/pnl endpoint shape ──
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
r = client.get("/dashboard/pnl")
check("4-1 pnl endpoint 200", r.status_code == 200)
body = r.json()
check("4-1 pnl exposes gross_revenue_usd", "gross_revenue_usd" in body)
check("4-1 pnl exposes etsy_fees_usd", "etsy_fees_usd" in body)
check("4-1 pnl exposes net_revenue_usd", "net_revenue_usd" in body)
check("4-1 pnl net = gross - fees", body["net_revenue_usd"] == round(body["gross_revenue_usd"] - body["etsy_fees_usd"], 2))
check("4-1 pnl profit = net - spend", body["profit_usd"] == round(body["net_revenue_usd"] - body["spend_usd"], 2))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-F tests passed.")
