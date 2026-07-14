from fastapi import APIRouter, HTTPException

from app.services import etsy_oauth
from app.services.etsy_client import EtsyClient
from config import settings

router = APIRouter()
etsy_client = EtsyClient()


@router.get("/oauth/login")
def etsy_oauth_login():
    url = etsy_oauth.build_authorization_url()
    return {"authorization_url": url}


@router.get("/production-partners")
async def etsy_production_partners():
    """List the shop's declared production partners + their ids — copy the id you
    want into ETSY_PRODUCTION_PARTNER_ID (needed before enabling POD). If it's
    empty, add Printify as a production partner in Etsy Shop Manager first."""
    try:
        data = await etsy_client.get_production_partners()
        results = data.get("results", []) or []
        return {
            "shop_id": settings.ETSY_SHOP_ID,
            "count": len(results),
            "partners": [
                {"production_partner_id": p.get("production_partner_id"),
                 "partner_name": p.get("partner_name"),
                 "location": p.get("location")}
                for p in results
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch production partners: {e}")


@router.get("/oauth/callback")
async def etsy_oauth_callback(code: str, state: str):
    try:
        from app.services import etsy_oauth
        token_data = await etsy_oauth.exchange_code_for_token(code, state)
        return {"status": "connected", "expires_in": token_data.get("expires_in")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")


@router.post("/listings/upload")
async def upload_listing(listing: dict):
    try:
        result = await etsy_client.create_draft_listing(listing)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Etsy upload failed: {e}")