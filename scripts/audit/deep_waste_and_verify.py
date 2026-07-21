"""DEEP AUDIT V2 — blocked-task waste, per-listing image readback, pinterest reality."""
import sqlite3, os, sys, json, asyncio, time
sys.path.insert(0, "/app")
import httpx
from config import settings
from app.services import etsy_oauth

DB = "/data/app.db"
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()
IMG = float(getattr(settings, "IMAGE_COST_USD", 0.04))
out = {}

# 1) billable images spent on BLOCKED tasks (pure waste — produced nothing)
blocked_ids = [r[0] for r in c.execute(
  "SELECT id FROM tasks WHERE json_extract(output_data,'$.pipeline_status')='BLOCKED_NO_PRODUCT'").fetchall()]
completed_ids = [r[0] for r in c.execute(
  "SELECT id FROM tasks WHERE json_extract(output_data,'$.pipeline_status')='COMPLETED'").fetchall()]
def bimg(ids):
    if not ids: return 0
    qs = ",".join("?"*len(ids))
    return c.execute(f"SELECT COUNT(*) FROM image_assets WHERE provider LIKE 'openrouter%' AND task_id IN ({qs})", ids).fetchone()[0]
tot_billable = c.execute("SELECT COUNT(*) FROM image_assets WHERE provider LIKE 'openrouter%'").fetchone()[0]
out["billable_images_total"] = tot_billable
out["billable_images_blocked"] = bimg(blocked_ids)
out["billable_images_completed"] = bimg(completed_ids)
out["blocked_image_waste_usd"] = round(bimg(blocked_ids)*IMG, 2)
out["n_blocked"] = len(blocked_ids); out["n_completed"] = len(completed_ids)

# 2) per-listing image readback (authoritative endpoint) for a real sample of 6
async def verify_images(listing_ids):
    tok = await etsy_oauth.get_valid_access_token()
    key = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
    H = {"x-api-key": key, "Authorization": f"Bearer {tok}"}
    res = {}
    async with httpx.AsyncClient(timeout=30) as cl:
        for lid in listing_ids:
            r = await cl.get(f"https://openapi.etsy.com/v3/application/listings/{lid}/images", headers=H)
            if r.status_code == 200:
                imgs = r.json().get("results", [])
                res[str(lid)] = {"count": len(imgs), "dims": [f"{i.get('full_width')}x{i.get('full_height')}" for i in imgs[:3]]}
            else:
                res[str(lid)] = f"HTTP {r.status_code}"
            time.sleep(0.6)
    return res
sample = [r[0] for r in c.execute("SELECT DISTINCT listing_id FROM image_assets WHERE listing_id IS NOT NULL ORDER BY listing_id DESC LIMIT 6").fetchall()]
out["perlisting_image_readback"] = asyncio.run(verify_images(sample))

# 3) pinterest success reality — inspect payloads + whether sandbox at the time
succ = c.execute("SELECT created_at, external_id, external_url, substr(payload,1,200) p FROM marketing_posts WHERE channel='pinterest' AND status='success' ORDER BY created_at").fetchall()
out["pinterest_success"] = [dict(r) for r in succ]
out["pinterest_sandbox_now"] = bool(getattr(settings, "PINTEREST_SANDBOX", False))

# 4) tag under-fill full population (all active-listing-mapped tasks)
out["note"] = "images column in CSV is the BULK endpoint (unreliable, may be 0); readback above is authoritative"

print(json.dumps(out, indent=2, default=str))
con.close()
