from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.services.tumblr_oauth import build_authorization_url, exchange_code_for_token

router = APIRouter()

_EXPECTED_CALLBACK = "https://kind-liberation-production.up.railway.app/tumblr/oauth/callback"


async def _complete_oauth(code: str, state: str):
    try:
        token_data = await exchange_code_for_token(code, state)
        return {"status": "connected", "expires_in": token_data.get("expires_in"), "scope": token_data.get("scope")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")


@router.get("/oauth/login")
async def tumblr_oauth_login(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    # If Tumblr bounced back with an OAuth error, surface it (don't re-loop).
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Tumblr OAuth error: {error} — {error_description or 'no description'}. "
                   f"Expected callback: {_EXPECTED_CALLBACK}",
        )
    # Observed in production: Tumblr delivers the authorization code to THIS
    # endpoint (the app's registered callback URL resolves to /oauth/login, not
    # /oauth/callback). Complete the exchange here instead of redirecting back
    # to the consent screen (which would discard the code and loop forever).
    if code and state:
        return await _complete_oauth(code, state)
    # No code yet → start the flow.
    url = build_authorization_url()
    return RedirectResponse(url)


@router.get("/oauth/callback")
async def tumblr_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    if error:
        raise HTTPException(status_code=400, detail=f"Tumblr OAuth error: {error} — {error_description or 'no description'}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing 'code' or 'state' in callback")
    return await _complete_oauth(code, state)
