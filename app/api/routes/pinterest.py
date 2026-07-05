from fastapi import APIRouter, HTTPException

from app.services.pinterest_oauth import build_authorization_url, exchange_code_for_token

router = APIRouter()


@router.get("/oauth/login")
def pinterest_oauth_login():
    url = build_authorization_url()
    return {"authorization_url": url}


@router.get("/oauth/callback")
async def pinterest_oauth_callback(code: str, state: str):
    try:
        token_data = await exchange_code_for_token(code, state)
        return {"status": "connected", "expires_in": token_data.get("expires_in")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")