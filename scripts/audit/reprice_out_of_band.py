"""
DEEP AUDIT V2 #13 — reprice LIVE listings that are outside their format price band.

Early (pre-band-enforcement) listings include coloring pages priced €15.00 when the
coloring_page band is (2.00, 4.50) — near-guaranteed zero-conversion. This clamps
each active listing's price into its format band (mid-band by default, or the
nearest edge). Dry-run unless --apply. Run INSIDE the container (has Etsy OAuth).

Usage:
  python scripts/audit/reprice_out_of_band.py            # dry-run
  python scripts/audit/reprice_out_of_band.py --apply
"""
import sqlite3, os, sys, asyncio, time
sys.path.insert(0, "/app")
import httpx
from config import settings
from app.services import etsy_oauth
from app.core.product_formats import price_band_for, snap_charm

DB = "/data/app.db"
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; c = con.cursor()


def task_type_for_listing(lid):
    row = c.execute("SELECT task_id FROM image_assets WHERE listing_id=? LIMIT 1", (str(lid),)).fetchone()
    tid = row[0] if row else None
    if not tid:
        row = c.execute("SELECT id FROM tasks WHERE json_extract(output_data,'$.listing_id')=? LIMIT 1", (str(lid),)).fetchone()
        tid = row[0] if row else None
    if not tid:
        return None
    r = c.execute("SELECT type FROM tasks WHERE id=?", (tid,)).fetchone()
    return r[0] if r else None


async def run(apply: bool):
    tok = await etsy_oauth.get_valid_access_token()
    shop = settings.ETSY_SHOP_ID
    key = f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"
    H = {"x-api-key": key, "Authorization": f"Bearer {tok}"}
    from app.services.etsy_client import EtsyClient
    fixed = scanned = 0
    async with httpx.AsyncClient(timeout=30) as cl:
        offset = 0
        while True:
            r = await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/active",
                             headers=H, params={"limit": 100, "offset": offset})
            r.raise_for_status()
            res = r.json().get("results", []) or []
            if not res:
                break
            for L in res:
                scanned += 1
                lid = L.get("listing_id")
                p = L.get("price") or {}
                price = (p.get("amount", 0) / (p.get("divisor") or 100))
                ttype = task_type_for_listing(lid)
                if not ttype:
                    continue
                lo, hi = price_band_for(ttype)
                if lo <= price <= hi:
                    continue
                target = snap_charm(min(hi, max(lo, (lo + hi) / 2)), ttype)
                fixed += 1
                print(f"listing {lid} ({ttype}): {price:.2f} -> {target:.2f} (band {lo}-{hi})")
                if apply:
                    await EtsyClient().update_listing(str(lid), {"price": target})
                    await asyncio.sleep(1.0)
            offset += 100
    print(f"\nScanned {scanned}; {'repriced' if apply else 'would reprice'} {fixed} out-of-band listing(s).")


if __name__ == "__main__":
    asyncio.run(run(apply="--apply" in sys.argv))
    con.close()
