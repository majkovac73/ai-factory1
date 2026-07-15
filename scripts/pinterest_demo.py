"""
Pinterest Standard-access demo script.

Run this against REAL production-limited (trial) access — NOT sandbox. It is meant
to be screen-recorded end to end: it prints clear step banners (which double as
on-screen captions / a voiceover script) and pauses at the OAuth step so you can
complete the real Pinterest login/consent in a browser before continuing.

It reuses the EXACT production code paths — the same OAuth flow, board listing,
and Pin-publishing the live app uses — so the video proves the real integration,
not a demo-only reimplementation:
  - Auth URL + token exchange: the deployed app's /pinterest/oauth/login +
    /pinterest/oauth/callback (pinterest_oauth.build_authorization_url /
    exchange_code_for_token). We hit the RUNNING app over HTTP for login so the
    OAuth `state` is stored by the same process that handles the callback.
  - Account info:  pinterest_oauth.get_user_account()  (GET /v5/user_account)
  - Boards:        pinterest_oauth.list_boards()        (GET /v5/boards)
  - Publish a Pin: MarketingRefreshService.refresh_post(...) -> PinterestChannel
                   (POST /v5/pins) — the real marketing path, using a real shop
                   product's asset + Etsy listing URL.

Run it INSIDE the deployed container (so it shares the DB, the image volume, and
the same process that services the public callback URL):

    railway ssh
    python scripts/pinterest_demo.py

(Or point --base-url at the public URL if you run it elsewhere; Phase 2 still
needs the container's DB + image assets to publish a real Pin.)
"""
import argparse
import asyncio
import sys
import time
import webbrowser

import httpx

# Real modules (verified against the codebase).
from config import settings
from app.services import pinterest_oauth
from app.marketing.pinterest_channel import PinterestChannel
from app.services.marketing_refresh_service import MarketingRefreshService
from app.services.pinterest_backfill_service import PinterestBackfillService


def banner(step: str, text: str):
    print("\n" + "=" * 70)
    print(f"STEP {step}: {text}")
    print("=" * 70, flush=True)


def phase1_authenticate(base_url: str):
    sandbox = bool(getattr(settings, "PINTEREST_SANDBOX", False))
    env = "SANDBOX (api-sandbox.pinterest.com)" if sandbox else "PRODUCTION (api.pinterest.com)"
    banner("1", f"Pinterest authentication — environment: {env}")
    print(f"App ID:        {settings.PINTEREST_APP_ID}")
    print(f"Redirect URI:  {settings.PINTEREST_REDIRECT_URI}")
    print("(Confirm this App ID is the correct one before recording.)\n")

    # Sandbox with a dashboard-generated token: no browser OAuth needed — the
    # token authenticates every call. (Trial-access apps can create Pins here.)
    if sandbox and getattr(settings, "PINTEREST_SANDBOX_TOKEN", None):
        print("Using the generated SANDBOX access token — authenticating with it directly.\n")
        banner("2", "Confirming the token is accepted by Pinterest (sandbox)")
        try:
            account = asyncio.run(pinterest_oauth.get_user_account())
        except Exception as e:
            print(f"Sandbox token was rejected ({e}). Check PINTEREST_SANDBOX_TOKEN and its scopes. Aborting.")
            sys.exit(1)
        print(f"Authenticated as: {account.get('username')} "
              f"(business: {account.get('business_name')}, id: {account.get('id')})", flush=True)
        return None  # boards are fetched/created in phase 2

    # Ask the RUNNING app for the auth URL so the OAuth `state` is stored by the
    # process that will handle the callback (avoids 'Unknown or expired state').
    try:
        r = httpx.get(f"{base_url}/pinterest/oauth/login", timeout=30)
        r.raise_for_status()
        auth_url = r.json()["authorization_url"]
    except Exception as e:
        print(f"Could not reach {base_url}/pinterest/oauth/login: {e}")
        print("Run this inside the deployed container (railway ssh) or pass "
              "--base-url https://kind-liberation-production.up.railway.app")
        sys.exit(1)

    print("Open this URL in a browser and complete Pinterest's real login + consent:\n")
    print(f"    {auth_url}\n", flush=True)
    try:
        webbrowser.open(auth_url)  # best-effort; no-op on a headless container
    except Exception:
        pass

    input(">>> After you click 'Allow' on Pinterest's real consent screen and the "
          "callback page shows \"connected\", press Enter to continue...\n")

    banner("2", "Confirming the callback stored a real access token")
    # Prove the token works by making a real authenticated read against Pinterest.
    try:
        boards = asyncio.run(pinterest_oauth.list_boards())
    except Exception as e:
        print(f"No working token yet ({e}) — authentication did not complete. Aborting.")
        sys.exit(1)
    print(f"Authenticated: a real token is stored and Pinterest accepted it "
          f"(read {len(boards)} board(s)).", flush=True)
    return boards


def phase2_core_features(boards):
    banner("3", "Fetching the authenticated user's Pinterest account info")
    account = asyncio.run(pinterest_oauth.get_user_account())
    print(f" username:      {account.get('username')}")
    print(f" business name: {account.get('business_name')}")
    print(f" account id:    {account.get('id')}")
    print(f" account type:  {account.get('account_type')}")
    print(f" board count:   {account.get('board_count')}", flush=True)
    time.sleep(1)

    banner("4", "Listing the boards available to this account")
    if not boards:
        boards = asyncio.run(pinterest_oauth.list_boards())
    for b in boards:
        print(f" - {b.get('name')} (id={b.get('id')}, {b.get('privacy')})")
    if not boards:
        # A fresh sandbox account has no boards — create one so there's a real
        # destination for the Pin (also demonstrates board creation).
        print("No boards yet — creating one to pin to...")
        nb = asyncio.run(pinterest_oauth.create_board("AI Factory Demo", "Demo board"))
        boards = [{"id": nb.get("id"), "name": nb.get("name"), "privacy": nb.get("privacy")}]
        print(f" + created board '{boards[0]['name']}' (id={boards[0]['id']})")
    # Pin to the first board (in sandbox this is the sandbox board), regardless of
    # the PINTEREST_BOARD_ID env — so the demo is self-contained.
    settings.PINTEREST_BOARD_ID = boards[0]["id"]
    time.sleep(1)

    banner("5", "Publishing a REAL Pin from a real shop product (the live marketing path)")
    refresh = MarketingRefreshService()
    # Pick a genuine published product that still has an image asset on disk.
    chosen = None
    for c in PinterestBackfillService().candidates(include_already_pinned=True):
        if refresh._pick_asset_path(c["task_id"]):
            chosen = c
            break
    if not chosen:
        print("No published product with an on-disk image asset was found to pin. "
              "Aborting (run this in the container so the image volume is available).")
        sys.exit(1)

    print(f" product:  {chosen['title']}")
    print(f" asset:    {refresh._pick_asset_path(chosen['task_id'])}")
    print(f" links to: https://www.etsy.com/listing/{chosen['listing_id']}")
    print(" publishing to Pinterest via the real PinterestChannel...", flush=True)

    result = refresh.refresh_post(
        chosen["task_id"], PinterestChannel(),
        listing_id=chosen["listing_id"], rewrite_caption=False,
    )

    if result.get("success"):
        pin_id = result.get("external_id")
        print(f"Pin created. id={pin_id}", flush=True)

        banner("6", "Reading the Pin back from Pinterest to confirm it landed")
        try:
            pin = asyncio.run(pinterest_oauth.get_pin(pin_id))
            print(f" confirmed on Pinterest: id={pin.get('id')}, board_id={pin.get('board_id')}, "
                  f"title={pin.get('title')!r}", flush=True)
        except Exception as e:
            print(f" (could not read the pin back: {e})", flush=True)

        # Production has a public page; sandbox pins aren't public, so only open
        # a browser when we're in production.
        if not getattr(settings, "PINTEREST_SANDBOX", False):
            pin_url = result.get("url") or f"https://www.pinterest.com/pin/{pin_id}/"
            print(f"Pin URL: {pin_url}")
            try:
                webbrowser.open(pin_url)
            except Exception:
                pass
            input(">>> Confirm the Pin is visible on the real Pinterest page, then press Enter to finish...\n")

        banner("7", "Demo complete")
        print("Authentication and the core Pinterest features (account read, board "
              "listing/creation, and publishing a Pin that lands on a real board) were "
              "all demonstrated with real results.", flush=True)
        return

    # Pin publish failed — most likely the app is still on TRIAL access, which
    # Pinterest blocks from creating Pins in production. Show this clearly rather
    # than crashing: it's exactly the limitation this Standard-access request lifts.
    err = str(result.get("error") or "")
    banner("6", "Pin publish returned an error from Pinterest")
    print(f"Pinterest response: {err}\n", flush=True)
    if "Trial access" in err or "403" in err or "Standard" in err:
        print("NOTE: This is the TRIAL-access limitation — Pinterest does not allow "
              "Pin creation in production until the app is granted STANDARD access "
              "(the access this demo requests). Authentication, account read, and "
              "board listing above all succeeded live against production pinterest.com "
              "using the real user-authorized token; Pin publishing uses that same "
              "token and the same code path, and will succeed once Standard access "
              "is granted.", flush=True)
    banner("7", "Demo complete")
    print("Real OAuth authentication + account read + board listing demonstrated live; "
          "Pin publishing is the core feature pending Standard access.", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000",
                    help="Base URL of the RUNNING deployed app that handles the OAuth callback "
                         "(default http://localhost:8000 for railway ssh; else the public URL).")
    args = ap.parse_args()

    boards = phase1_authenticate(args.base_url.rstrip("/"))
    phase2_core_features(boards)


if __name__ == "__main__":
    main()
