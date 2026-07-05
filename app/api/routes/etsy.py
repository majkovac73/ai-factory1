from fastapi import APIRouter, HTTPException, Request

from app.services.etsy_oauth import build_authorization_url, exchange_code_for_token
from app.services.etsy_client import EtsyClient
from app.services.task_service import TaskService

router = APIRouter()
etsy_client = EtsyClient()
task_service = TaskService()


@router.get("/oauth/login")
def etsy_oauth_login():
    url = build_authorization_url()
    return {"authorization_url": url}


@router.get("/oauth/callback")
async def etsy_oauth_callback(code: str, state: str):
    try:
        token_data = await exchange_code_for_token(code, state)
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