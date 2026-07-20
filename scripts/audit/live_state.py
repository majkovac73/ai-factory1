import os, json, asyncio
os.environ.setdefault("PYTHONWARNINGS","ignore")
from config import settings
import sqlite3
DB="/data/app.db"

print("=== KEY CONFIG (live prod) ===")
for k in ["AUTONOMY_ENABLED","AUTO_PUBLISH_LISTINGS","MARKETING_REFRESH_ENABLED","PRODUCT_SCORE_ENFORCE",
          "PRODUCT_MIN_SCORE","PRODUCT_JUDGE_FLOOR","PRODUCT_DET_FLOOR","CONCEPT_MODEL","DEFAULT_MODEL",
          "PINTEREST_SANDBOX","AUTONOMY_INTERVAL_MINUTES","AUTONOMY_SCHEDULE_SECONDS","DAILY_TASK_CAP",
          "DAILY_SPEND_CAP_USD","SEASONAL_PRODUCT_RATIO","ETSY_SHOP_ID","MAX_PDF_PAGES","FACTORY_API_KEY"]:
    v=getattr(settings,k,"<MISSING>")
    if k=="FACTORY_API_KEY": v="SET" if v else "EMPTY"
    print(f"  {k} = {v}")

print("\n=== COST / SPEND analytics events ===")
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
for r in c.execute("select event_type,count(*),sum(coalesce(value,0)) from analytics_events where event_type like '%cost%' or event_type like '%spend%' or event_type like '%image_gen%' or event_type like '%token%' group by event_type"):
    print("  ",tuple(r))
print("  (all event_types:)", [r[0] for r in c.execute("select distinct event_type from analytics_events")])

print("\n=== ETSY LIVE READBACK ===")
async def etsy():
    import httpx
    from app.services import etsy_oauth
    try:
        tok=await etsy_oauth.get_valid_access_token()
    except Exception as e:
        print("  token error:",e); return
    shop=settings.ETSY_SHOP_ID
    H={"x-api-key":settings.ETSY_API_KEY,"Authorization":f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=30) as cl:
        r=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}",headers=H)
        print("  shop status",r.status_code)
        if r.status_code==200:
            d=r.json(); print("   shop:",{k:d.get(k) for k in ("shop_name","listing_active_count","digital_listing_count","review_count","transaction_sold_count","is_vacation","announcement")})
        # active listings count + sample
        r=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/active?limit=3&includes=Images",headers=H)
        print("  active listings status",r.status_code)
        if r.status_code==200:
            d=r.json(); print("   active count:",d.get("count"))
            for L in d.get("results",[])[:3]:
                price=L.get("price") or {}
                print("   listing",L.get("listing_id"),"|",str(L.get("title"))[:60])
                print("       state=",L.get("state"),"price=",price.get("amount"),"/",price.get("divisor"),price.get("currency_code"),
                      "taxonomy=",L.get("taxonomy_id"),"who_made=",L.get("who_made"),"is_digital=",L.get("is_digital"),
                      "num_images=",len(L.get("images",[]) or []),"views=",L.get("views"),"tags=",len(L.get("tags",[]) or []))
            # files for first listing (digital deliverable present?)
            if d.get("results"):
                lid=d["results"][0]["listing_id"]
                rf=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/{lid}/files",headers=H)
                print("   files status",rf.status_code, "count", (rf.json().get("count") if rf.status_code==200 else rf.text[:120]))
asyncio.run(etsy())
