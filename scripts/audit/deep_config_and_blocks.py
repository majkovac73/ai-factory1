"""DEEP AUDIT V2 — block-reason buckets, live config, alert-state, EtsyMarket header."""
import sqlite3, json, os, glob

DB = os.environ.get("AUDIT_DB", "/data/app.db")
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()
out = {}

# block reasons bucketed by leading phrase (before first ':')
rows = c.execute(
  "SELECT json_extract(output_data,'$.pipeline_blocked_reason') r FROM tasks "
  "WHERE json_extract(output_data,'$.pipeline_status')='BLOCKED_NO_PRODUCT'").fetchall()
buckets = {}
for (r,) in rows:
    key = (r or "unknown").split(":")[0].split("—")[0].strip()[:55]
    buckets[key] = buckets.get(key, 0) + 1
out["block_buckets"] = sorted(buckets.items(), key=lambda x: -x[1])

# live settings snapshot (money/quality knobs)
try:
    from config import settings as s
    keys = ["CONCEPT_MODEL","DEFAULT_MODEL","SEO_MODEL","CONTENT_QA_MODEL","PDF_QA_MODEL",
            "PRODUCT_SCORE_ENFORCE","PRODUCT_MIN_SCORE","AUTONOMY_ENABLED","AUTO_PUBLISH_LISTINGS",
            "MARKETING_REFRESH_ENABLED","MAX_DAILY_SPEND_USD","MAX_TASKS_PER_DAY",
            "PINTEREST_SANDBOX","PINTEREST_CAN_PUBLISH","PINTEREST_APP_ID",
            "BACKUP_S3_BUCKET","IMAGE_COST_USD","LISTING_IMAGE_SIZE","LISTING_HERO_W"]
    snap = {}
    for k in keys:
        v = getattr(s, k, "<missing>")
        if k in ("CONCEPT_MODEL","DEFAULT_MODEL") or "MODEL" in k:
            snap[k] = v
        else:
            snap[k] = v
    # secret lengths only (never values)
    snap["PINTEREST_APP_SECRET_len"] = len(getattr(s,"PINTEREST_APP_SECRET","") or "")
    snap["OPENROUTER_API_KEY_set"] = bool(getattr(s,"OPENROUTER_API_KEY",None))
    out["settings"] = snap
except Exception as e:
    out["settings"] = f"ERR {e}"

# zero-production / alert marker files on the volume
markers = {}
for f in glob.glob("/data/*.json"):
    b = os.path.basename(f)
    if "alert" in b or "streak" in b or "production" in b:
        try: markers[b] = json.load(open(f))
        except Exception as e: markers[b] = f"ERR {e}"
out["alert_markers"] = markers

# EtsyMarketService header construction (read the source in-container)
try:
    src = open("/app/app/services/etsy_market_service.py").read()
    import re
    hits = [l.strip() for l in src.splitlines() if "x-api-key" in l or "api_key_header" in l or "ETSY_API_KEY" in l or "SHARED_SECRET" in l]
    out["etsy_market_header_lines"] = hits[:12]
except Exception as e:
    out["etsy_market_header_lines"] = f"ERR {e}"

# how many completed tasks actually have a resolvable listing_id (published for real)
out["completed_with_listing"] = c.execute(
  "SELECT COUNT(*) FROM tasks WHERE json_extract(output_data,'$.listing_id') IS NOT NULL").fetchone()[0]

print(json.dumps(out, indent=2, default=str))
con.close()
