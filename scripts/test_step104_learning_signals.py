"""
Step 104-C test — learning-loop signal fixes (2-1, 2-2).

2-1: get_best_product_insights ranks by REVENUE first; a zero-sale/zero-view
     product never appears; a product with a sale outranks any zero-sale one;
     reliability never qualifies; anti-signal lists $0-revenue formats.
2-2: engagement_velocity uses per-day deltas — a new+steep listing beats an
     old+flat one, in both the score and refresh priority.

Usage: python scripts/test_step104_learning_signals.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "signals.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.models.analytics_event import AnalyticsEvent

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)


def add_task(tid, ttype="single_print", title="T", kws=None):
    db = SessionLocal()
    try:
        db.add(Task(id=tid, prompt="p", type=ttype, status="DONE", input_data={},
                    output_data={"title": title, "keywords": kws or ["kw"]}))
        db.commit()
    finally:
        db.close()


def add_stat(tid, views, favs, days_ago):
    db = SessionLocal()
    try:
        db.add(AnalyticsEvent(id=f"{tid}-{days_ago}", event_type="listing_stats", entity_type="task",
                              entity_id=tid, value=float(views + 10 * favs),
                              payload={"views": views, "favorites": favs},
                              created_at=datetime.utcnow() - timedelta(days=days_ago)))
        db.commit()
    finally:
        db.close()


from app.services.performance_service import PerformanceService
from app.services.best_products_service import BestProductsService
from app.services.revenue_service import RevenueService

ps = PerformanceService()

# ── 2-2 velocity: old+flat vs new+steep ──
add_task("old", title="Old flat")
add_stat("old", 100, 0, days_ago=1)   # high cumulative, but...
add_stat("old", 98, 0, days_ago=8)    # ...barely moved in a week -> ~0.3/day
add_task("new", title="New steep")
add_stat("new", 30, 1, days_ago=1)    # 30 views + 1 fav now
add_stat("new", 5, 0, days_ago=4)     # was 5 three days ago -> (25 + 10)/3 ≈ 11.7/day

v_old = ps.engagement_velocity("old")
v_new = ps.engagement_velocity("new")
check("2-2 new+steep velocity > old+flat velocity", v_new > v_old)
check("2-2 old flat velocity is low", v_old < 2)
check("2-2 engagement score rewards velocity", ps._engagement_score("new") > ps._engagement_score("old"))

# ── 2-1 insights: no sales -> velocity ranking, honest label ──
ins = BestProductsService().get_best_product_insights(limit=10)
check("2-1 no sales -> has_sales False", ins["has_sales"] is False)
check("2-1 label says 'No sales yet'", "No sales yet" in ins["label"])
titles = [p["title"] for p in ins["products"]]
check("2-1 velocity leader (New steep) ranks first", titles and titles[0] == "New steep")

# a zero-view zero-sale product never appears
add_task("ghost", title="Ghost no views")
ins2 = BestProductsService().get_best_product_insights(limit=10)
check("2-1 zero-view zero-sale product excluded", "Ghost no views" not in [p["title"] for p in ins2["products"]])

# ── 2-1 revenue outranks everything ──
RevenueService().record_sale("old", 12.0, transaction_id="tx-old")
ins3 = BestProductsService().get_best_product_insights(limit=10)
check("2-1 a sale flips has_sales True", ins3["has_sales"] is True)
check("2-1 the sold product ranks first over higher-velocity ones",
      ins3["products"] and ins3["products"][0]["task_id"] == "old")

# ── 2-1 anti-signal: $0-revenue formats with >=3 listings ──
for i in range(3):
    add_task(f"cp{i}", ttype="coloring_page", title=f"CP {i}")
ins4 = BestProductsService().get_best_product_insights(limit=10)
zero = dict(ins4["zero_revenue_formats"])
check("2-1 anti-signal flags coloring_page ($0, >=3 listings)", zero.get("coloring_page", 0) >= 3)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104-C learning-signal tests passed.")
