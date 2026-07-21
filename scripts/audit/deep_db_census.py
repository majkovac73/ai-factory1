"""
DEEP AUDIT V2 — full-population census of the production DB (/data/app.db).

Run INSIDE the Railway container (reads WAL automatically):
    railway ssh "cd /app && echo <b64> | base64 -d > /tmp/c.py && python3 /tmp/c.py"

Prints a machine-readable census: every table + row count, and full distributions
for the tables the audit reasons about (tasks, analytics_events, marketing_posts,
logs, image_assets). No sampling — COUNT/GROUP BY over the whole table.
"""
import sqlite3, json, os

DB = os.environ.get("AUDIT_DB", "/data/app.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
c = con.cursor()

def q(sql, args=()):
    return [dict(r) for r in c.execute(sql, args).fetchall()]

out = {"db": DB, "db_size_bytes": os.path.getsize(DB)}

# every table + row count
tables = [r["name"] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
out["tables"] = {}
for t in tables:
    try:
        n = c.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
    except Exception as e:
        n = f"ERR {e}"
    out["tables"][t] = n

def dist(table, col, extra=""):
    try:
        return q(f"SELECT {col} AS k, COUNT(*) AS n FROM '{table}' {extra} GROUP BY {col} ORDER BY n DESC")
    except Exception as e:
        return f"ERR {e}"

# tasks
out["tasks_status"] = dist("tasks", "status")
out["tasks_type"] = dist("tasks", "type")
out["tasks_retry_count"] = dist("tasks", "retry_count")
try:
    out["tasks_pipeline_status"] = q(
        "SELECT json_extract(output_data,'$.pipeline_status') AS k, COUNT(*) n "
        "FROM tasks GROUP BY k ORDER BY n DESC")
except Exception as e:
    out["tasks_pipeline_status"] = f"ERR {e}"
try:
    r = c.execute("SELECT MIN(created_at), MAX(created_at) FROM tasks").fetchone()
    out["tasks_date_range"] = [r[0], r[1]]
except Exception as e:
    out["tasks_date_range"] = f"ERR {e}"

# analytics_events
out["analytics_event_type"] = dist("analytics_events", "event_type")
try:
    r = c.execute("SELECT MIN(created_at), MAX(created_at) FROM analytics_events").fetchone()
    out["analytics_date_range"] = [r[0], r[1]]
except Exception as e:
    out["analytics_date_range"] = f"ERR {e}"

# marketing_posts
out["marketing_channel_status"] = dist("marketing_posts", "channel || ':' || status")
# distinct error messages for pinterest failures (root-cause the 403s)
try:
    out["pinterest_errors"] = q(
        "SELECT substr(error_message,1,80) AS k, COUNT(*) n FROM marketing_posts "
        "WHERE channel='pinterest' AND status='failed' GROUP BY k ORDER BY n DESC")
except Exception as e:
    out["pinterest_errors"] = f"ERR {e}"

# logs
out["logs_level"] = dist("logs", "level")
try:
    out["logs_top_messages"] = q(
        "SELECT substr(message,1,70) AS k, level, COUNT(*) n FROM logs "
        "GROUP BY k, level ORDER BY n DESC LIMIT 40")
    r = c.execute("SELECT MIN(created_at), MAX(created_at) FROM logs").fetchone()
    out["logs_date_range"] = [r[0], r[1]]
except Exception as e:
    out["logs_top_messages"] = f"ERR {e}"

# image_assets
out["image_use_case"] = dist("image_assets", "use_case")
out["image_provider_model"] = dist("image_assets", "provider || ':' || model")

# revenue-related event detail
for et in ("sale_recorded", "fee_estimate", "cost_incurred", "listing_stats", "concept_scored", "trend_signal"):
    try:
        n = c.execute("SELECT COUNT(*) FROM analytics_events WHERE event_type=?", (et,)).fetchone()[0]
        s = c.execute("SELECT COALESCE(SUM(value),0) FROM analytics_events WHERE event_type=?", (et,)).fetchone()[0]
        out[f"ev_{et}"] = {"count": n, "sum_value": s}
    except Exception as e:
        out[f"ev_{et}"] = f"ERR {e}"

print(json.dumps(out, indent=2, default=str))
con.close()
