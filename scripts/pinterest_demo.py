"""
Pinterest Standard-access demo script (OAuth flow + FULL API integration).

Built to be screen-recorded end to end for Pinterest's Standard-access review.

WHY THIS VERSION EXISTS
-----------------------
The first submission was denied because the video showed the OAuth flow but NOT
a completed Pin-creation integration ("API usage is not visible... show how you
created it and also display the newly created Pin"). The reason it wasn't shown:
a TRIAL-access app is BLOCKED from creating Pins in PRODUCTION (403, code 29), so
the production Pin step could never succeed on camera. Pinterest's own denial
message points to the fix: demonstrate the integration in the SANDBOX environment
(https://developer.pinterest.com/docs/developer-tools/sandbox/), where a Trial
app CAN create Pins.

So this recording shows BOTH things the reviewer requires, end to end:

  1. OAUTH FLOW (production pinterest.com) — you open the real consent screen and
     click "Allow"; the app exchanges the code for a token and reads the connected
     account. Proves real user authentication. (Phases 1-2.)

  2. FULL API INTEGRATION with visible PROCESS + RESULTS (sandbox) — the app
     creates a real Pin via POST /v5/pins using the SAME production code path
     (app.marketing.pinterest_channel.PinterestChannel), then reads it back via
     GET /v5/pins/{id} and lists the board's Pins — printing the raw request
     summary and full JSON responses so the create call, the returned Pin id, and
     the Pin's data (image, link, title, board) are all visible on screen.
     (Phases 3-5.) Sandbox Pins are not on the public pinterest.com site, so the
     API responses ARE the way to display the created Pin — exactly as Pinterest's
     guidance describes.

Same production code paths, not a demo-only reimplementation:
  - Auth URL + token exchange: the deployed app's /pinterest/oauth/login +
    /pinterest/oauth/callback (pinterest_oauth.build_authorization_url /
    exchange_code_for_token).
  - Account info:  pinterest_oauth.get_user_account()  (GET /v5/user_account)
  - Boards:        pinterest_oauth.list_boards() / create_board()  (/v5/boards)
  - Create a Pin:  PinterestChannel._post_async(...)  (POST /v5/pins)
  - Read it back:  pinterest_oauth.get_pin(pin_id)  (GET /v5/pins/{id})

PREREQUISITES
-------------
  * Production (Phase 1-2): the DEPLOYED app that services the public callback URL
    must be in production mode (PINTEREST_SANDBOX unset/false) with the correct
    production App ID (1589935), secret, and redirect URI.
  * Sandbox (Phase 3-5): generate a SANDBOX access token in the Pinterest
    developer portal (your app -> Sandbox tab -> "Generate token", with
    boards:read, boards:write, pins:read, pins:write) and provide it via
    PINTEREST_SANDBOX_TOKEN (env) or --sandbox-token. The script creates a
    sandbox board + Pin for you, so no other setup is needed.

Run it INSIDE the deployed container (shares the DB + image volume + the process
that services the public callback URL):

    railway ssh
    PINTEREST_SANDBOX_TOKEN=<your-sandbox-token> python scripts/pinterest_demo.py

(Or point --base-url at the public URL if you run it elsewhere.)
"""
import argparse
import asyncio
import json
import os
import sys
import time
import webbrowser

# Make the repo root importable when run as `python scripts/pinterest_demo.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

# Real modules (verified against the codebase).
from config import settings
from app.services import pinterest_oauth
from app.marketing.pinterest_channel import PinterestChannel


def banner(step: str, text: str):
    print("\n" + "=" * 70)
    print(f"STEP {step}: {text}")
    print("=" * 70, flush=True)


def _pretty(obj) -> str:
    try:
        return json.dumps(obj, indent=2)[:4000]
    except Exception:
        return str(obj)[:4000]


# ── PHASE 1: production OAuth ────────────────────────────────────────────────
def phase1_authenticate(base_url: str):
    """Force production for the OAuth flow — the review must see the real
    pinterest.com consent screen (the sandbox has no consent screen)."""
    settings.PINTEREST_SANDBOX = False

    banner("0", "What this recording shows")
    print("1) OAUTH FLOW (production pinterest.com): you authorize the app.")
    print("2) FULL API INTEGRATION (sandbox): the app CREATES a Pin and DISPLAYS")
    print("   the created Pin via the API responses (create + read-back + list).\n")
    print("PREREQUISITES (confirm before recording):")
    print(f"  App ID:        {settings.PINTEREST_APP_ID}")
    print(f"  Redirect URI:  {settings.PINTEREST_REDIRECT_URI}")
    print("  OAuth env:     PRODUCTION (api.pinterest.com) — sandbox disabled here.")
    print("  Sandbox token: " + ("SET" if _sandbox_token() else "MISSING (see Phase 3 note)"))
    print(flush=True)

    banner("1", "Authorize the app on Pinterest (OAuth) — do this before anything else")
    try:
        r = httpx.get(f"{base_url}/pinterest/oauth/login", timeout=30)
        r.raise_for_status()
        auth_url = r.json()["authorization_url"]
    except Exception as e:
        print(f"Could not reach {base_url}/pinterest/oauth/login: {e}")
        print("Run this inside the deployed container (railway ssh) or pass "
              "--base-url https://kind-liberation-production.up.railway.app")
        sys.exit(1)

    print("Open this authorization link, sign in to Pinterest, and click 'Allow':\n")
    print("    " + "-" * 62)
    print(f"    {auth_url}")
    print("    " + "-" * 62 + "\n", flush=True)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    input(">>> After you click 'Allow' on Pinterest's consent screen and the page "
          "shows \"connected\", press Enter to continue...\n")

    banner("2", "Authorization complete — confirming the connected account (GET /v5/user_account)")
    try:
        account = asyncio.run(pinterest_oauth.get_user_account())
    except Exception as e:
        print(f"No working token yet ({e}) — authorization did not complete. Aborting.")
        sys.exit(1)
    print("Connected. GET /v5/user_account returned:")
    print(_pretty(account), flush=True)
    time.sleep(1)
    return account


# ── PHASE 3-5: sandbox Pin-creation integration ─────────────────────────────
def _sandbox_token(cli_token: str = None) -> str:
    return cli_token or getattr(settings, "PINTEREST_SANDBOX_TOKEN", None) or os.getenv("PINTEREST_SANDBOX_TOKEN")


def _sandbox_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def phase_sandbox_integration(sandbox_token: str):
    """The core requirement: CREATE a Pin and DISPLAY it, with the process and
    results visible. Done in the sandbox because a Trial-access app cannot create
    Pins in production (403 code 29) — per Pinterest's own denial guidance."""
    banner("3", "FULL API INTEGRATION (sandbox): why sandbox for Pin creation")
    print("A Trial-access app is blocked from creating Pins in PRODUCTION (403, code")
    print("29). Pinterest's review guidance says to demonstrate the integration in the")
    print("SANDBOX environment, where Pin creation works. The code below is the SAME")
    print("production path (PinterestChannel -> POST /v5/pins); only the API host +")
    print("token are the sandbox ones.\n", flush=True)

    if not sandbox_token:
        print("MISSING sandbox token. Generate one in the Pinterest developer portal:")
        print("  your app (1589935) -> Sandbox tab -> Generate token")
        print("  scopes: boards:read, boards:write, pins:read, pins:write")
        print("Then re-run with PINTEREST_SANDBOX_TOKEN=<token> (or --sandbox-token).")
        sys.exit(1)

    # Switch THIS process's Pinterest calls to sandbox, authenticated by the
    # dashboard-generated sandbox token (get_valid_access_token returns it directly
    # in sandbox mode — no OAuth needed against the sandbox).
    settings.PINTEREST_SANDBOX = True
    settings.PINTEREST_SANDBOX_TOKEN = sandbox_token
    api = pinterest_oauth.api_base()
    print(f"Sandbox API base: {api}", flush=True)

    # 3a — confirm the sandbox account
    try:
        acct = asyncio.run(pinterest_oauth.get_user_account())
        print("\nGET /v5/user_account (sandbox) ->")
        print(_pretty(acct), flush=True)
    except Exception as e:
        print(f"Could not read sandbox account (token invalid/expired?): {e}")
        sys.exit(1)

    # 3b — a board to publish to (create one if the sandbox account has none)
    banner("4", "Create/select a sandbox board, then CREATE a Pin (POST /v5/pins)")
    boards = asyncio.run(pinterest_oauth.list_boards())
    if boards:
        board = boards[0]
        print(f"Using existing sandbox board: {board.get('name')} (id={board.get('id')})")
    else:
        board = asyncio.run(pinterest_oauth.create_board("AI Factory Demo", "Standard-access demo board"))
        print(f"Created sandbox board via POST /v5/boards: id={board.get('id')} name={board.get('name')}")
    board_id = board["id"]
    settings.PINTEREST_BOARD_ID = board_id

    # 3c — CREATE the Pin through the real production code path
    listing = {
        "title": "Botanical Line Art Print — Printable Wall Art",
        "description": "Minimalist botanical line art printable — demo Pin created via the Pinterest API.",
        "listing_url": "https://www.etsy.com/shop/CardsForAllOcDesigns",
        "image_base64": _demo_image_b64(),
        "image_content_type": "image/png",
    }
    print("\nCreating a Pin via PinterestChannel -> POST /v5/pins with payload:")
    print(_pretty({k: (v if k != "image_base64" else f"<{len(v)} base64 chars>") for k, v in listing.items()}))
    print("... calling Pinterest ...", flush=True)

    result = PinterestChannel().post(listing)
    if not result.get("success"):
        print(f"\nPin creation FAILED: {result.get('error')}")
        print("Check the sandbox token scopes (needs pins:write, boards:write) and that")
        print("PINTEREST_SANDBOX_TOKEN is a SANDBOX token, not a production one.")
        sys.exit(1)

    pin_id = result.get("external_id")
    print(f"\nPin CREATED. POST /v5/pins returned id = {pin_id}", flush=True)

    # 3d — DISPLAY the created Pin: read it back + show full JSON
    banner("5", "DISPLAY the newly created Pin (GET /v5/pins/{id}) + list the board's Pins")
    pin = asyncio.run(pinterest_oauth.get_pin(pin_id))
    print(f"GET /v5/pins/{pin_id} ->")
    print(_pretty(pin), flush=True)

    # also list the board's pins to show it now contains the new Pin
    try:
        pins = _list_board_pins(api, sandbox_token, board_id)
        print(f"\nGET /v5/boards/{board_id}/pins -> {len(pins)} pin(s); ids: "
              f"{[p.get('id') for p in pins][:10]}", flush=True)
        print("(The created Pin id above appears in this board's Pin list — proof it landed.)")
    except Exception as e:
        print(f"(board pin list read failed, non-fatal: {e})")

    banner("6", "Demo complete — both requirements shown")
    print("Shown live and end to end:")
    print("  1) OAUTH FLOW on production pinterest.com (consent + token + account read).")
    print("  2) FULL API INTEGRATION in sandbox: a Pin CREATED via POST /v5/pins and")
    print("     DISPLAYED via GET /v5/pins/{id} (+ the board's Pin list) — the create")
    print("     call, the returned Pin id, and the Pin's image/link/title are all")
    print("     visible in the API responses above.")
    print("\nOnce Standard access is granted, the identical code path runs against")
    print("PRODUCTION and the Pin appears on the public pinterest.com profile.", flush=True)


def _list_board_pins(api: str, token: str, board_id: str) -> list:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{api}/boards/{board_id}/pins", headers=_sandbox_headers(token), params={"page_size": 25})
        r.raise_for_status()
        return r.json().get("items", []) or []


def _demo_image_b64() -> str:
    """A small, valid PNG so the Pin has real media even if no shop asset is on
    disk (keeps the sandbox demo self-contained). Pinterest requires an image."""
    import base64
    from io import BytesIO
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (1000, 1500), (245, 244, 240))
        d = ImageDraw.Draw(img)
        d.rectangle([80, 80, 920, 1420], outline=(60, 90, 70), width=6)
        for i, y in enumerate(range(300, 1300, 120)):
            d.line([200, y, 800, y - (60 if i % 2 else 0)], fill=(60, 90, 70), width=5)
        d.text((120, 140), "Botanical Line Art\n(demo Pin)", fill=(40, 60, 45))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # 1x1 transparent PNG fallback (Pinterest still accepts it as media).
        return ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m"
                "NkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000",
                    help="Base URL of the RUNNING deployed app that handles the OAuth callback "
                         "(default http://localhost:8000 for railway ssh; else the public URL).")
    ap.add_argument("--sandbox-token", default=None,
                    help="Pinterest SANDBOX access token (or set PINTEREST_SANDBOX_TOKEN). "
                         "Required for the Pin-creation integration (Phases 3-5).")
    ap.add_argument("--skip-oauth", action="store_true",
                    help="Skip the production OAuth phase and run only the sandbox Pin "
                         "integration (useful for a second take of just Phases 3-5).")
    args = ap.parse_args()

    token = _sandbox_token(args.sandbox_token)
    if not args.skip_oauth:
        phase1_authenticate(args.base_url.rstrip("/"))
    phase_sandbox_integration(token)


if __name__ == "__main__":
    main()
