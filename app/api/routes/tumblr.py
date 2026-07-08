from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.services.tumblr_oauth import build_authorization_url, exchange_code_for_token

router = APIRouter()


@router.get("/oauth/login")
def tumblr_oauth_login():
    # Redirect straight to Tumblr's consent screen (one-time manual step for Maj),
    # mirroring the Etsy/Pinterest flows.
    url = build_authorization_url()
    return RedirectResponse(url)


@router.get("/oauth/callback")
async def tumblr_oauth_callback(code: str, state: str):
    try:
        token_data = await exchange_code_for_token(code, state)
        return {"status": "connected", "expires_in": token_data.get("expires_in"), "scope": token_data.get("scope")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")
