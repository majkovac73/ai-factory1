"""Audit: production DB overview — tables, row counts, and key status
distributions. Run in the Railway container against /data/app.db:
    railway ssh "python3 -" < scripts/audit/db_overview.py   (or base64 pipe)
Read-only.
"""
import sqlite3, os, json
DB = "/data/app.db" if os.path.exists("/data/app.db") else os.environ.get("DATABASE_PATH", "app.db")
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
print("DB:", DB)
print("TABLES:", len(tables))
for t in tables:
    try:
        n = c.execute(f"select count(*) from {t}").fetchone()[0]
    except Exception as e:
        n = f"ERR {e}"
    print(f"  {t:32s} {n}")

def dist(table, col):
    try:
        rows = c.execute(f"select {col}, count(*) from {table} group by {col} order by 2 desc").fetchall()
        return {str(r[0]): r[1] for r in rows}
    except Exception as e:
        return f"ERR {e}"

print("\n=== tasks.status ==>", dist("tasks","status"))
print("=== tasks.task_type ==>", dist("tasks","task_type"))
# guess listing/product tables
for cand in ["etsy_listings","listings","products","pod_products","image_assets","marketing_posts","marketing_post","analytics_events","analytics_event","fulfillment_records","concept_scores"]:
    if cand in tables:
        cols = [d[1] for d in c.execute(f"PRAGMA table_info({cand})")]
        print(f"\n--- {cand} cols: {cols}")
