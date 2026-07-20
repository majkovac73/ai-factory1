import asyncio
from collections import Counter
from config import settings
async def e():
    import httpx
    from app.services import etsy_oauth
    tok=await etsy_oauth.get_valid_access_token()
    shop=settings.ETSY_SHOP_ID
    H={"x-api-key":f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}","Authorization":f"Bearer {tok}"}
    allL=[]; off=0
    async with httpx.AsyncClient(timeout=40) as cl:
        while True:
            r=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/active?limit=100&offset={off}&includes=Images",headers=H)
            if r.status_code!=200: print("ERR",r.status_code,r.text[:120]); break
            res=r.json().get("results",[]); allL+=res
            if len(res)<100: break
            off+=100
    print("total active pulled:",len(allL))
    zero_img=[L["listing_id"] for L in allL if len(L.get("images",[]) or [])==0]
    imgc=Counter(len(L.get("images",[]) or []) for L in allL)
    print("image-count distribution (imgs->#listings):", dict(sorted(imgc.items())))
    print("listings with ZERO images:", len(zero_img), zero_img[:10])
    states=Counter(L.get("state") for L in allL); print("states:",dict(states))
    tax=Counter(L.get("taxonomy_id") for L in allL); print("taxonomy_id dist:",dict(tax))
    prices=[]
    for L in allL:
        p=L.get("price") or {}
        if p.get("divisor"): prices.append(round(p["amount"]/p["divisor"],2))
    prices.sort()
    if prices:
        import statistics
        print(f"prices n={len(prices)} min={prices[0]} p50={statistics.median(prices)} max={prices[-1]} currency={ (allL[0].get('price') or {}).get('currency_code')}")
        print("  price values:",prices)
    tagc=Counter(len(L.get("tags",[]) or []) for L in allL); print("tag-count dist:",dict(tagc))
    # truncated tags (len==20 → likely cut)
    trunc=[t for L in allL for t in (L.get("tags") or []) if len(t)>=20]
    print("tags at 20-char limit (likely truncated):",len(trunc), trunc[:8])
asyncio.run(e())
