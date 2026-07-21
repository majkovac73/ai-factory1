"""DEEP AUDIT V2 — failure mining + spend + block root-cause (full population)."""
import sqlite3, json, os, glob

DB = os.environ.get("AUDIT_DB", "/data/app.db")
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()
def q(sql, a=()): return [dict(r) for r in c.execute(sql, a).fetchall()]
out = {}

# 1) blocked-reason root causes (full population of the 90 blocks)
out["blocked_reasons"] = q(
  "SELECT substr(json_extract(output_data,'$.pipeline_blocked_reason'),1,70) AS reason, "
  "type, COUNT(*) n FROM tasks "
  "WHERE json_extract(output_data,'$.pipeline_status')='BLOCKED_NO_PRODUCT' "
  "GROUP BY reason, type ORDER BY n DESC")
# blocked rate by type
out["blocked_by_type"] = q(
  "SELECT type, "
  "SUM(CASE WHEN json_extract(output_data,'$.pipeline_status')='BLOCKED_NO_PRODUCT' THEN 1 ELSE 0 END) blocked, "
  "SUM(CASE WHEN json_extract(output_data,'$.pipeline_status')='COMPLETED' THEN 1 ELSE 0 END) completed, "
  "COUNT(*) total FROM tasks GROUP BY type ORDER BY total DESC")

# 2) the 402 / autonomy cycle errors — full timeline
out["error_logs"] = q(
  "SELECT created_at, substr(message,1,140) msg FROM logs "
  "WHERE level IN ('ERROR','CRITICAL') ORDER BY created_at")
out["warning_logs"] = q(
  "SELECT created_at, substr(message,1,120) msg FROM logs "
  "WHERE level='WARNING' ORDER BY created_at")

# 3) concept_scored full stats + pass rate
r = c.execute("SELECT COUNT(*) n, AVG(value) mean, MIN(value) mn, MAX(value) mx FROM analytics_events WHERE event_type='concept_scored'").fetchone()
out["concept_scored_stats"] = dict(r)
out["concept_passed"] = q(
  "SELECT json_extract(payload,'$.passed') passed, COUNT(*) n FROM analytics_events "
  "WHERE event_type='concept_scored' GROUP BY passed")

# 4) pinterest successes — real vs sandbox (inspect external_id/url/payload)
out["pinterest_success_detail"] = q(
  "SELECT created_at, external_id, substr(external_url,1,60) url, substr(error_message,1,40) err "
  "FROM marketing_posts WHERE channel='pinterest' AND status='success' ORDER BY created_at")
# pinterest failure timeline (first/last)
r = c.execute("SELECT MIN(created_at), MAX(created_at), COUNT(*) FROM marketing_posts WHERE channel='pinterest' AND status='failed'").fetchone()
out["pinterest_fail_range"] = list(r)

# 5) spend history from autonomy_state json files (full 14 days)
spend = {}
for f in sorted(glob.glob("/data/autonomy_state_*.json")):
    try:
        d = json.load(open(f))
        spend[os.path.basename(f)] = d
    except Exception as e:
        spend[os.path.basename(f)] = f"ERR {e}"
out["autonomy_state_files"] = spend
tot = sum((v.get("spend_usd",0) if isinstance(v,dict) else 0) for v in spend.values())
tot_tasks = sum((v.get("tasks_created",0) if isinstance(v,dict) else 0) for v in spend.values())
out["total_spend_usd"] = round(tot, 4)
out["total_tasks_created_ledger"] = tot_tasks

# 6) cost_incurred detail (per use_case) since ledger deployed
out["cost_by_use_case"] = q(
  "SELECT json_extract(payload,'$.use_case') uc, COUNT(*) n, ROUND(SUM(value),4) usd "
  "FROM analytics_events WHERE event_type='cost_incurred' GROUP BY uc ORDER BY usd DESC")

# 7) listing_stats: real total views (latest per task) — full population
out["listing_stats_sample"] = q(
  "SELECT json_extract(payload,'$.views') views, json_extract(payload,'$.favorites') favs, COUNT(*) n "
  "FROM analytics_events WHERE event_type='listing_stats' GROUP BY views, favs ORDER BY n DESC LIMIT 20")

print(json.dumps(out, indent=2, default=str))
con.close()
