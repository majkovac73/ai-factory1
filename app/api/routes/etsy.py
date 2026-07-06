from fastapi import APIRouter, HTTPException

from app.services import etsy_oauth
from app.services.etsy_client import EtsyClient

router = APIRouter()
etsy_client = EtsyClient()


@router.get("/oauth/login")
def etsy_oauth_login():
    url = etsy_oauth.build_authorization_url()
    return {"authorization_url": url}


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