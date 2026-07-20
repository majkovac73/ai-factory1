"""
Pinterest Standard-access demo script (OAuth flow + real app usage).

This is built to be screen-recorded end to end for Pinterest's Standard-access
review. Pinterest asks the video to show two things:

  1. HOW THE APP AUTHENTICATES A PINTEREST USER  -> the OAuth flow. The script's
     FIRST action is to print an authorization link. You open it, log in, and
     click "Allow" on Pinterest's real consent screen. NOTHING else runs until
     that authorization completes — the reviewer sees the whole grant happen.

  2. AN EXAMPLE OF APP USAGE  -> how a user actually uses the app. Once you're
     connected, the app: confirms which Pinterest account is linked, lists the
     boards it can publish to, and publishes a REAL marketing Pin for one of the
     shop's real products (linking back to its Etsy listing), then confirms the
     Pin is live.

It reuses the EXACT production code paths — the same OAuth flow, board listing,
and Pin publishing the live app uses — so the video proves the real integration,
not a demo-only reimplementation:
  - Auth URL + token exchange: the deployed app's /pinterest/oauth/login +
    /pinterest/oauth/callback (pinterest_oauth.build_authorization_url /
    exchange_code_for_token). We hit the RUNNING app over HTTP for the login URL
    so the OAuth `state` is stored by the same process that handles the callback.
  - Account info:  pinterest_oauth.get_user_account()  (GET /v5/user_account)
  - Boards:        pinterest_oauth.list_boards()        (GET /v5/boards)
  - Publish a Pin: MarketingRefreshService.refresh_post(...) -> PinterestChannel
                   (POST /v5/pins) — the real marketing path, using a real shop
                   product's asset + Etsy listing URL.

This demo ALWAYS runs against PRODUCTION pinterest.com (never the sandbox). The
sandbox never shows the real consent screen and its Pins aren't publicly
viewable, so it can't satisfy the review. The script forces production mode for
its own API calls; see the PREREQUISITES banner it prints — the deployed app
that handles the callback must ALSO be in production mode (PINTEREST_SANDBOX
unset/false) with the correct production App ID, secret, and redirect URI.

Run it INSIDE the deployed container (so it shares the DB, the image volume, and
the same process that services the public callback URL):

    railway ssh
    python scripts/pinterest_demo.py

(Or point --base-url at the public URL if you run it elsewhere; the publish step
still needs the container's DB + image assets to publish a real Pin.)
"""
import argparse
import asyncio
import os
import sys
import time
import webbrowser

# Make the repo root importable when run as `python scripts/pinterest_demo.py`
# (otherwise only scripts/ is on sys.path and `config`/`app` aren't found).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def _force_production():
    """The review video must show real pinterest.com — never the sandbox. Force
    this process's Pinterest calls (auth-URL generation for our own reads, token
    exchange, and every API read below) onto production, regardless of any
    PINTEREST_SANDBOX/PINTEREST_SANDBOX_TOKEN left in the environment."""
    settings.PINTEREST_SANDBOX = False
    settings.PINTEREST_SANDBOX_TOKEN = None


def phase1_authenticate(base_url: str):
    _force_production()

    banner("0", "What this recording shows")
    print("1) The Pinterest OAuth flow — you authorize the app on pinterest.com.")
    print("2) Real app usage — the app then reads your account, lists your boards,")
    print("   and publishes a real marketing Pin for a shop product.\n")
    print("PREREQUISITES (confirm before recording):")
    print(f"  App ID:        {settings.PINTEREST_APP_ID}")
    print(f"  Redirect URI:  {settings.PINTEREST_REDIRECT_URI}")
    print("  Environment:   PRODUCTION (api.pinterest.com) — sandbox is disabled.")
    print("  The DEPLOYED app handling the callback must also be in production mode")
    print("  (PINTEREST_SANDBOX unset/false) with this same App ID + secret.\n", flush=True)

    # ── STEP 1: the authorization link comes FIRST ───────────────────────────
    # Ask the RUNNING app for the auth URL so the OAuth `state` is stored by the
    # process that will handle the callback (avoids 'Unknown or expired state').
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
        webbrowser.open(auth_url)  # best-effort; no-op on a headless container
    except Exception:
        pass

    input(">>> After you click 'Allow' on Pinterest's consent screen and the page "
          "shows \"connected\", press Enter to continue...\n")

    # ── STEP 2: prove the authorization worked (real token now stored) ───────
    banner("2", "Authorization complete — confirming the connected account")
    # Reading the account proves the token the consent produced is real and works.
    try:
        account = asyncio.run(pinterest_oauth.get_user_account())
    except Exception as e:
        print(f"No working token yet ({e}) — authorization did not complete. Aborting.")
        print("Re-run and make sure you finished 'Allow' and the callback showed connected.")
        sys.exit(1)
    print(" You are now connected. The app can act on Pinterest as:")
    print(f"   username:      {account.get('username')}")
    print(f"   business name: {account.get('business_name')}")
    print(f"   account id:    {account.get('id')}")
    print(f"   account type:  {account.get('account_type')}")
    print(f"   board count:   {account.get('board_count')}", flush=True)
    time.sleep(1)
    return account


def phase2_core_features(account):
    banner("3", "App usage: listing the boards the app can publish to")
    boards = asyncio.run(pinterest_oauth.list_boards())
    for b in boards:
        print(f" - {b.get('name')} (id={b.get('id')}, {b.get('privacy')})")
    if not boards:
        # No board on the account — create one so there's a real destination for
        # the Pin (also demonstrates the app creating a board on the user's behalf).
        print("No boards yet — creating one to publish to...")
        nb = asyncio.run(pinterest_oauth.create_board("AI Factory Demo", "Demo board"))
        boards = [{"id": nb.get("id"), "name": nb.get("name"), "privacy": nb.get("privacy")}]
        print(f" + created board '{boards[0]['name']}' (id={boards[0]['id']})")
    # Publish to the first board so the demo is self-contained regardless of the
    # PINTEREST_BOARD_ID env.
    settings.PINTEREST_BOARD_ID = boards[0]["id"]
    print(f"\n The app will publish to: {boards[0]['name']} (id={boards[0]['id']})", flush=True)
    time.sleep(1)

    banner("4", "App usage: publishing a REAL marketing Pin for a shop product")
    print(" This is the core feature — the app turns a real shop product into a Pin")
    print(" that links back to its Etsy listing, using the live marketing path.\n", flush=True)
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

        banner("5", "Confirming the Pin landed on the real board")
        try:
            pin = asyncio.run(pinterest_oauth.get_pin(pin_id))
            print(f" confirmed on Pinterest: id={pin.get('id')}, board_id={pin.get('board_id')}, "
                  f"title={pin.get('title')!r}", flush=True)
        except Exception as e:
            print(f" (could not read the pin back: {e})", flush=True)

        pin_url = result.get("url") or f"https://www.pinterest.com/pin/{pin_id}/"
        print(f"Pin URL: {pin_url}")
        try:
            webbrowser.open(pin_url)
        except Exception:
            pass
        input(">>> Confirm the Pin is visible on the real Pinterest page, then press Enter to finish...\n")

        banner("6", "Demo complete")
        print("Shown live: the OAuth authorization flow, and real app usage — account "
              "read, board listing, and publishing a Pin that links a shop product to "
              "its Etsy listing and lands on a real board.", flush=True)
        return

    # Pin publish failed — most likely the app is still on TRIAL access, which
    # Pinterest blocks from creating Pins in production. Show this clearly rather
    # than crashing: it's exactly the limitation this Standard-access request lifts.
    err = str(result.get("error") or "")
    banner("5", "Pin publish returned an error from Pinterest")
    print(f"Pinterest response: {err}\n", flush=True)
    if "Trial access" in err or "403" in err or "Standard" in err:
        print("NOTE: This is the TRIAL-access limitation — Pinterest does not allow "
              "Pin creation in production until the app is granted STANDARD access "
              "(the access this demo requests). The OAuth authorization, account read, "
              "and board listing above all ran live against production pinterest.com "
              "using the real user-authorized token; Pin publishing uses that same "
              "token and the same code path, and will succeed once Standard access "
              "is granted.", flush=True)
    banner("6", "Demo complete")
    print("Real OAuth authorization + account read + board listing demonstrated live; "
          "Pin publishing is the core feature pending Standard access.", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000",
                    help="Base URL of the RUNNING deployed app that handles the OAuth callback "
                         "(default http://localhost:8000 for railway ssh; else the public URL).")
    args = ap.parse_args()

    account = phase1_authenticate(args.base_url.rstrip("/"))
    phase2_core_features(account)


if __name__ == "__main__":
    main()
