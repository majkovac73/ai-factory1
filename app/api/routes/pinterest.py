from fastapi import APIRouter, HTTPException

from app.services.pinterest_oauth import (
    build_authorization_url,
    exchange_code_for_token,
    disconnect as pinterest_disconnect,
    list_boards as pinterest_list_boards,
)

router = APIRouter()


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


@router.post("/disconnect")
def pinterest_disconnect_route():
    """Disconnect Pinterest and permanently delete all Pinterest-derived data
    (OAuth token + recorded Pins) — the deletion promise in /privacy. Mutating,
    so it is protected by FACTORY_API_KEY when that is set."""
    return pinterest_disconnect()