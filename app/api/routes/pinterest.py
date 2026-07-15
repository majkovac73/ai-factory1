from fastapi import APIRouter, HTTPException

from app.services.pinterest_oauth import (
    build_authorization_url,
    exchange_code_for_token,
    disconnect as pinterest_disconnect,
    list_boards as pinterest_list_boards,
    get_user_account as pinterest_get_user_account,
)

router = APIRouter()


@router.get("/account")
async def pinterest_account():
    """The connected Pinterest account's basic profile (which account is linked)."""
    try:
        return await pinterest_get_user_account()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch account (is Pinterest connected?): {e}")


@router.get("/oauth/login")
def pinterest_oauth_login():
    url = build_authorization_url()
    return {"authorization_url": url}


@router.get("/boards")
async def pinterest_boards():
    """List the connected account's boards + ids — copy the id you want into the
    PINTEREST_BOARD_ID env var. Requires an OAuth token (connect via
    /pinterest/oauth/login first)."""
    try:
        boards = await pinterest_list_boards()
        return {"connected": True, "count": len(boards), "boards": boards}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not list boards (is Pinterest connected?): {e}")


@router.get("/oauth/callback")
async def pinterest_oauth_callback(code: str, state: str):
    try:
        token_data = await exchange_code_for_token(code, state)
        return {"status": "connected", "expires_in": token_data.get("expires_in")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth exchange failed: {e}")


@router.post("/set-token")
def pinterest_set_token(body: dict):
    """Store a manually-generated Pinterest access token (from the app dashboard's
    "generate token" — an alternative to the full OAuth flow). The token MUST be a
    PRODUCTION token (not sandbox) and MUST include the boards:read, boards:write,
    pins:read, pins:write scopes, or pin creation will 401. Optional refresh_token
    + expires_in; without a refresh token it simply stops working when it expires
    (re-paste a new one). Mutating -> protected by FACTORY_API_KEY."""
    from app.services.pinterest_oauth import save_token
    access_token = (body or {}).get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="access_token is required")
    save_token({
        "access_token": access_token,
        "refresh_token": (body or {}).get("refresh_token", ""),
        "expires_in": int((body or {}).get("expires_in", 30 * 24 * 3600)),  # default 30d
    })
    return {"status": "token stored", "has_refresh_token": bool((body or {}).get("refresh_token"))}


@router.post("/disconnect")
def pinterest_disconnect_route():
    """Disconnect Pinterest and permanently delete all Pinterest-derived data
    (OAuth token + recorded Pins) — the deletion promise in /privacy. Mutating,
    so it is protected by FACTORY_API_KEY when that is set."""
    return pinterest_disconnect()