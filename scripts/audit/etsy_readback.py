import asyncio
from config import settings
async def e():
    import httpx
    from app.services import etsy_oauth
    tok=await etsy_oauth.get_valid_access_token()
    shop=settings.ETSY_SHOP_ID
    key=f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"  # keystring:sharedsecret
    H={"x-api-key":key,"Authorization":f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=30) as cl:
        r=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}",headers=H)
        print("shop ->",r.status_code)
        if r.status_code==200:
            d=r.json()
            print("  ",{k:d.get(k) for k in ("shop_name","listing_active_count","digital_listing_count","review_count","review_average","transaction_sold_count","is_vacation")})
            print("  announcement:",str(d.get("announcement"))[:120])
        r=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/active?limit=5&includes=Images,Inventory",headers=H)
        print("active listings ->",r.status_code, "count=", (r.json().get("count") if r.status_code==200 else r.text[:150]))
        if r.status_code==200:
            for L in r.json().get("results",[])[:5]:
                p=L.get("price") or {}
                amt = (p.get("amount",0)/p.get("divisor",1)) if p.get("divisor") else None
                print(f"  {L.get('listing_id')} state={L.get('state')} ${amt} {p.get('currency_code')} tax={L.get('taxonomy_id')} who={L.get('who_made')} digital={L.get('is_digital')} imgs={len(L.get('images',[]) or [])} tags={len(L.get('tags',[]) or [])} views={L.get('views')} qty={L.get('quantity')}")
                print(f"       title: {str(L.get('title'))[:90]}")
                print(f"       tags: {L.get('tags')}")
                lid=L.get("listing_id")
                rf=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/{lid}/files",headers=H)
                if rf.status_code==200:
                    fs=rf.json(); print(f"       files: count={fs.get('count')} names={[f.get('filename') for f in fs.get('results',[])][:4]}")
                else:
                    print(f"       files -> {rf.status_code} {rf.text[:80]}")
                break  # detailed dump for first only
asyncio.run(e())
