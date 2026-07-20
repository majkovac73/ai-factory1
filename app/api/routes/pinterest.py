from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

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


def _oauth_result_page(title: str, heading: str, message: str, ok: bool) -> str:
    color = "#2e7d32" if ok else "#c62828"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 520px; margin: 80px auto; padding: 0 24px; text-align: center; line-height: 1.6;">
  <div style="font-size: 56px;">{'&#10003;' if ok else '&#10007;'}</div>
  <h1 style="color: {color}; margin: 8px 0 4px;">{heading}</h1>
  <p style="color: #444;">{message}</p>
  <p style="color: #888; font-size: 14px;">You can close this tab and return to the app.</p>
</body></html>"""


@router.get("/oauth/callback", response_class=HTMLResponse, include_in_schema=False)
async def pinterest_oauth_callback(code: str, state: str):
    """Pinterest redirects the user's browser here after they click "Allow".
    Returns a friendly HTML confirmation page (this is what the reviewer sees on
    screen during the demo), not raw JSON."""
    try:
        await exchange_code_for_token(code, state)
        return HTMLResponse(_oauth_result_page(
            "Connected — DesignsForAll",
            "Pinterest account connected",
            "Your Pinterest account is now authorized. The app can publish Pins on your behalf.",
            ok=True,
        ))
    except Exception as e:
        return HTMLResponse(
            _oauth_result_page(
                "Connection failed — DesignsForAll",
                "Connection failed",
                f"We couldn't complete the Pinterest authorization: {e}",
                ok=False,
            ),
            status_code=400,
        )


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