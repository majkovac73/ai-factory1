"""DEEP AUDIT V2 — full-catalog unit economics CSV (every published listing).

Runs IN the container. Pulls every active Etsy listing (paginated, readback), joins
to the originating task + its real billable image count (image_assets, seedream =
$IMAGE_COST_USD each) + views (listing_stats) + revenue (sale_recorded). Emits CSV
to stdout. Costs are REAL counts, not guesses; text/QA LLM cost is estimated per
the same constants the ledger uses and labeled as an estimate column.
"""
import sqlite3, os, sys, csv, io, time, asyncio
sys.path.insert(0, "/app")
import httpx
from config import settings
from app.services import etsy_oauth

DB = "/data/app.db"
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()

IMG = float(getattr(settings, "IMAGE_COST_USD", 0.04))
VQA = float(getattr(settings, "VISION_QA_COST_USD", 0.002))
LISTING_FEE = 0.20

async def fetch_all_active():
    tok = await etsy_oauth.get_valid_access_token()
    shop = settings.ETSY_SHOP_ID
    key = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
    H = {"x-api-key": key, "Authorization": f"Bearer {tok}"}
    out, offset = [], 0
    async with httpx.AsyncClient(timeout=30) as cl:
        while True:
            r = await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/active",
                             headers=H, params={"limit": 100, "offset": offset})
            if r.status_code >= 400:
                print(f"# etsy active listings error {r.status_code}: {r.text[:150]}", file=sys.stderr); break
            res = r.json().get("results", []) or []
            out.extend(res)
            if len(res) < 100: break
            offset += 100; time.sleep(0.5)
    return out

def task_for_listing(lid):
    row = c.execute("SELECT task_id FROM image_assets WHERE listing_id=? LIMIT 1", (str(lid),)).fetchone()
    if row: return row[0]
    row = c.execute("SELECT id FROM tasks WHERE json_extract(output_data,'$.listing_id')=? LIMIT 1", (str(lid),)).fetchone()
    return row[0] if row else None

def billable_images(task_id):
    if not task_id: return 0
    return c.execute("SELECT COUNT(*) FROM image_assets WHERE task_id=? AND provider LIKE 'openrouter%'", (task_id,)).fetchone()[0]

def views_for(task_id):
    if not task_id: return 0
    row = c.execute("SELECT json_extract(payload,'$.views') FROM analytics_events "
                    "WHERE event_type='listing_stats' AND entity_id=? ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
    return int(row[0] or 0) if row and row[0] is not None else 0

def revenue_for(task_id):
    if not task_id: return 0.0
    row = c.execute("SELECT COALESCE(SUM(value),0) FROM analytics_events WHERE event_type='sale_recorded' AND entity_id=?", (task_id,)).fetchone()
    return float(row[0] or 0)

listings = asyncio.run(fetch_all_active())
w = csv.writer(sys.stdout)
w.writerow(["listing_id","task_id","type","state","price","currency","tags","images","views",
            "billable_images","est_image_cost","est_text_qa_cost","est_listing_fee","est_total_cost",
            "revenue","units_sold","est_profit","breakeven_units_at_price"])
tot = {"cost":0.0,"rev":0.0}
for L in listings:
    lid = L.get("listing_id")
    price = (L.get("price") or {})
    amt = (price.get("amount",0)/ (price.get("divisor") or 100))
    tid = task_for_listing(lid)
    bimg = billable_images(tid)
    ttype = (c.execute("SELECT type FROM tasks WHERE id=?", (tid,)).fetchone() or [""])[0] if tid else ""
    img_cost = round(bimg*IMG, 4)
    # text+QA estimate: ~3 text calls + ~2 QA per product (research/copy/seo + content QA)
    txtqa = round(3*float(getattr(settings,"TEXT_LLM_COST_USD",0.002)) + 2*VQA, 4)
    total = round(img_cost + txtqa + LISTING_FEE, 4)
    rev = revenue_for(tid)
    profit = round(rev - total, 4)
    be_units = round(total/amt, 2) if amt else ""
    w.writerow([lid, tid or "", ttype, L.get("state"), round(amt,2), price.get("currency_code"),
                len(L.get("tags",[]) or []), len(L.get("images",[]) or []), views_for(tid),
                bimg, img_cost, txtqa, LISTING_FEE, total, rev, int(rev>0), profit, be_units])
    tot["cost"] += total; tot["rev"] += rev
print(f"# TOTALS listings={len(listings)} est_total_cost=${tot['cost']:.2f} revenue=${tot['rev']:.2f} net=${tot['rev']-tot['cost']:.2f}", file=sys.stderr)
con.close()
