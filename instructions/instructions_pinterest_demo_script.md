# Instructions: Pinterest demo script for Standard API access video

## Context (read first)

Pinterest's Standard access upgrade requires a video demo showing:
1. How the app authenticates Pinterest users
2. The main Pinterest features the app uses
3. (Optional) a voiceover

This must be recorded against the app's real **"Production limited" /
trial access** (not sandbox) — the reviewer needs to see genuine OAuth
against real `pinterest.com` and a real Pin actually landing on a real
board. Do not use the sandbox environment or the manually-generated
sandbox access token anywhere in this script; the existing app
secret/OAuth flow already in production is what should be exercised here.

**Use production App ID `1589935`** (AUDIT 2026-07-20 #18: this is the current
production app; `1587865` is a superseded, different app — do not use it). Make
sure the redirect URI, app secret, and this script all target `1589935`.

The goal is a single script that produces a clean, narratable,
step-by-step console trace covering both authentication and the core
Pinterest features, so Maj can screen-record it running once (plus a
browser window for the OAuth redirect) and have everything Pinterest asked
for in one continuous take.

---

## Part A — `scripts/pinterest_demo.py`

This script has two phases: **Phase 1 (browser-driven)** shows real user
authentication; **Phase 2 (script-driven)** exercises the main features
using the resulting real token. Structure it so each step prints a clear,
numbered, human-readable banner — these banners double as on-screen
captions during recording and as a script Maj can read aloud for the
voiceover.

```python
"""
Pinterest Standard-access demo script.

Run this against real production-limited (trial) access — NOT sandbox.
Intended to be screen-recorded end to end: it prints clear step banners
so the recording is self-explanatory, and pauses at the auth step so Maj
can complete the real Pinterest login/consent in a browser window before
continuing.

Usage (via `railway ssh` against the deployed app, so the real redirect
URI — https://kind-liberation-production.up.railway.app/pinterest/oauth/callback
— resolves correctly):

    python scripts/pinterest_demo.py
"""
import sys
import time
import webbrowser

# Adjust these imports to match the real module paths in the repo.
from app.integrations.pinterest_client import PinterestClient  # verify actual path/class name first
from app.db.session import get_db_session  # verify actual path first


def banner(step: str, text: str):
    print("\n" + "=" * 70)
    print(f"STEP {step}: {text}")
    print("=" * 70)


def phase1_authenticate(client: PinterestClient):
    banner("1", "Starting Pinterest OAuth (PKCE) authentication")
    auth_url = client.build_authorization_url()  # verify real method name
    print(f"Opening browser to Pinterest's real login/consent page:\n{auth_url}\n")
    print("If the browser doesn't open automatically, open the URL above manually.")
    webbrowser.open(auth_url)

    input(
        "\n>>> Complete the login and click 'Allow' on Pinterest's real "
        "consent screen in the browser, then come back here and press "
        "Enter to continue...\n"
    )

    banner("2", "Confirming the OAuth callback was received and a real token was stored")
    # Verify against the real callback/storage path — this should read
    # whatever the /pinterest/oauth/callback route just persisted, not
    # re-implement the exchange here.
    token_record = client.get_latest_stored_token()  # verify real method name
    if not token_record:
        print("No token found — authentication did not complete. Aborting.")
        sys.exit(1)
    print(f"Authenticated Pinterest account connected. Token stored: {bool(token_record)}")
    return token_record


def phase2_core_features(client: PinterestClient, token_record):
    banner("3", "Fetching the authenticated user's Pinterest account info")
    account = client.get_account_info(token_record)  # verify real method name
    print(f"Connected account: {account}")
    time.sleep(1)

    banner("4", "Listing boards available to this account")
    boards = client.list_boards(token_record)  # verify real method name
    for b in boards:
        print(f" - {b.get('name')} (id={b.get('id')})")
    if not boards:
        print("No boards found — create at least one real board on the "
              "connected Pinterest account before recording, so this step "
              "has something real to show.")
        sys.exit(1)
    demo_board = boards[0]
    time.sleep(1)

    banner("5", "Creating a real demo Pin on that board")
    # Use a real existing product asset from the shop, not a placeholder
    # image, so this looks like genuine app usage rather than a synthetic
    # test. Verify the actual method/args used elsewhere in the codebase
    # for publishing a Pin (this likely already exists in the marketing
    # agent — reuse that, don't reimplement it here).
    pin = client.create_pin(
        token_record,
        board_id=demo_board["id"],
        image_path="<path to a real existing product image asset>",
        title="<a real product title>",
        description="<a real product description>",
        link="<the real Etsy listing URL this Pin promotes>",
    )
    print(f"Pin created: {pin}")

    pin_url = pin.get("url") or f"https://www.pinterest.com/pin/{pin.get('id')}/"
    banner("6", "Opening the real created Pin on pinterest.com to confirm it's live")
    print(f"Pin URL: {pin_url}")
    webbrowser.open(pin_url)
    input("\n>>> Confirm the Pin is visible on the real Pinterest page, then press Enter to finish...\n")

    banner("7", "Demo complete")
    print("Authentication and core feature usage both demonstrated with real, live results.")


def main():
    client = PinterestClient()  # verify real constructor/args
    token_record = phase1_authenticate(client)
    phase2_core_features(client, token_record)


if __name__ == "__main__":
    main()
```

### Notes for whoever implements this

- **Do not reimplement the OAuth exchange or Pin-creation logic inside
  this script.** Call into the existing `PinterestClient` (or whatever
  the real class/module is named) — reuse the exact code path already
  used in production, since the whole point of the demo is proving the
  real integration works, not a parallel demo-only implementation.
- Verify every method name referenced above (`build_authorization_url`,
  `get_latest_stored_token`, `get_account_info`, `list_boards`,
  `create_pin`) against the actual client class before running — these
  are illustrative names, not guaranteed to match.
- Before recording: create at least one real board on the connected
  Pinterest account if none exists yet, and have a real product image
  asset ready to use, so Step 5 publishes something genuine rather than a
  placeholder.
- Run this once, uninterrupted, all the way through as a rehearsal before
  the real recording — fix any errors so the actual take is clean.

## Part B — Recording it

1. Start screen recording (Xbog Game Bar / OBS / whatever Maj chose)
   before running the script.
2. Run `python scripts/pinterest_demo.py` via `railway ssh` (so the
   redirect URI matches the deployed app) with the browser window visible
   on screen alongside the terminal.
3. Let the script's own banners narrate the auth step and each feature
   step; add a live voiceover on top if desired, roughly following the
   banner text ("Now I'm authenticating with Pinterest... this redirects
   to Pinterest's real login... now the app lists my boards... now it
   publishes a real Pin... here it is live on Pinterest").
4. Stop recording after Step 7 confirms completion and the real Pin is
   visible in the browser.
5. Export as `.mp4`, keep it short (1–3 minutes is plenty), and upload it
   in the Standard access request form.

## Testing before recording for real

- Run the script once locally/via `railway ssh` as a dry run first — do
  not record the very first attempt, since it will likely surface a typo
  in a method name or missing board. Fix issues, rerun until it completes
  cleanly end to end with real output at every step, then record the
  clean run.
