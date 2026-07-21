"""DEEP AUDIT V2 #2 — root-cause the 'no verified PDF' block (actual errors)."""
import sqlite3, json, os
DB = os.environ.get("AUDIT_DB", "/data/app.db")
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()

# The block reason is generic; the SPECIFIC failure was logged. Pull PDF-related
# WARNING/ERROR log messages (persisted post-#14) and bucket them.
rows = c.execute(
  "SELECT substr(message,1,160) m, COUNT(*) n FROM logs "
  "WHERE (message LIKE '%PDF%' OR message LIKE '%page%' OR message LIKE '%planner%') "
  "AND level IN ('WARNING','ERROR') GROUP BY substr(message,1,80) ORDER BY n DESC").fetchall()
print("=== PDF-related WARNING/ERROR log buckets (persisted subset) ===")
for r in rows: print(f"  {r['n']:3}  {r['m']}")

# content-QA rejection reasons on pdf_planner tasks (from block reasons)
rows2 = c.execute(
  "SELECT substr(json_extract(output_data,'$.pipeline_blocked_reason'),1,120) r, COUNT(*) n "
  "FROM tasks WHERE type='pdf_planner_or_guide' "
  "AND json_extract(output_data,'$.pipeline_status')='BLOCKED_NO_PRODUCT' "
  "GROUP BY r ORDER BY n DESC").fetchall()
print("\n=== pdf_planner block reasons (all) ===")
for r in rows2: print(f"  {r['n']:3}  {r['r']}")

# how many pages did failed vs completed planners request?
rows3 = c.execute(
  "SELECT json_extract(output_data,'$.pipeline_status') st, "
  "json_extract(metadata_,'$.page_count') pc, COUNT(*) n "
  "FROM tasks WHERE type='pdf_planner_or_guide' GROUP BY st, pc ORDER BY st, pc").fetchall()
print("\n=== pdf_planner page_count x outcome ===")
for r in rows3: print(f"  status={r['st']}  page_count={r['pc']}  n={r['n']}")
con.close()
