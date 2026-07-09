from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.services.tumblr_oauth import build_authorization_url, exchange_code_for_token

router = APIRouter()


@router.get("/oauth/login")
def tumblr_oauth_login(error: Optional[str] = None, error_description: Optional[str] = None):
    # If Tumblr bounced the user back here with an OAuth error (e.g.
    # redirect_uri_mismatch), DO NOT re-redirect to the consent screen — that
    # creates an infinite reload loop that hides the real cause. Surface it.
    if error:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Tumblr OAuth error: {error} — {error_description or 'no description'}. "
                "The redirect_uri your app sends must exactly match one registered "
                "on the Tumblr app at https://www.tumblr.com/oauth/apps. Expected: "
                "https://kind-liberation-production.up.railway.app/tumblr/oauth/callback"
            ),
        )
    url = build_authorization_url()
    return RedirectResponse(url)


@router.get("/oauth/callback")
async def tumblr_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    # Tumblr may redirect to the callback itself with an error instead of a code.
    if error:
        raise HTTPException(status_code=400, detail=f"Tumblr OAuth error: {error} — {error_description or 'no description'}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing 'code' or 'state' in callback")
    try:
        token_data = await exchange_code_for_token(code, state)
        return {"status": "connected", "expires_in": token_data.get("expires_in"), "scope": token_data.get("scope")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")
