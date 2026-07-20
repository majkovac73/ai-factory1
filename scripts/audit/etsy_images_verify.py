import asyncio
from config import settings
async def e():
    import httpx
    from app.services import etsy_oauth
    tok=await etsy_oauth.get_valid_access_token()
    shop=settings.ETSY_SHOP_ID
    H={"x-api-key":f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}","Authorization":f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=40) as cl:
        # get 6 active listing ids
        r=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/active?limit=6",headers=H)
        ids=[L["listing_id"] for L in r.json().get("results",[])]
        print("checking listings:",ids)
        for lid in ids:
            ri=await cl.get(f"https://openapi.etsy.com/v3/application/listings/{lid}/images",headers=H)
            nimg=ri.json().get("count") if ri.status_code==200 else f"ERR{ri.status_code}"
            rf=await cl.get(f"https://openapi.etsy.com/v3/application/shops/{shop}/listings/{lid}/files",headers=H)
            nfil=rf.json().get("count") if rf.status_code==200 else f"ERR{rf.status_code}"
            # sample image dims
            dims=""
            if ri.status_code==200 and ri.json().get("results"):
                im=ri.json()["results"][0]; dims=f"{im.get('full_width')}x{im.get('full_height')}"
            print(f"  {lid}: images(GET /listings/id/images)={nimg} dims={dims}  files={nfil}")
asyncio.run(e())
