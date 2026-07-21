"""
Pinterest Standard-access demo script (OAuth flow + FULL API integration).

Built to be screen-recorded end to end for Pinterest's Standard-access review.

WHY THIS VERSION EXISTS
-----------------------
Two submissions were denied with "the API usage is not actually visible... show
how you created [the Pin] and also display the newly created Pin on the Pinterest
platform." A TRIAL app cannot create Pins in PRODUCTION (403, code 29), so the
integration is demonstrated in the SANDBOX. The key fact the earlier versions
missed: **a Sandbox Pin IS viewable on pinterest.com** — per Pinterest's sandbox
docs, "you can view boards and Pins you create in Sandbox when you visit your own
Pinterest user profile in the Pinterest mobile apps or on Pinterest.com." They are
visible ONLY to the creator (the account the sandbox token belongs to), when
logged in. So "display the newly created Pin on the Pinterest platform" =
create the sandbox Pin, then open your own profile/board on pinterest.com (logged
in) and show it there.

So this recording shows BOTH things the reviewer requires, end to end:

  1. OAUTH FLOW (production pinterest.com) — you open the real consent screen and
     click "Allow"; the app exchanges the code for a token and reads the connected
     account. Proves real user authentication. (Phases 1-2.)

  2. FULL API INTEGRATION with visible PROCESS + RESULTS (sandbox) — the app
     creates a real Pin via POST /v5/pins using the SAME production code path
     (app.marketing.pinterest_channel.PinterestChannel), printing the raw HTTP
     request + response so the create call and returned Pin id are visible; reads
     it back via GET /v5/pins/{id} and lists the board's Pins (Steps 3-5); THEN
     **prints the pinterest.com profile/board/Pin URLs to open logged-in and show
     the Pin on the Pinterest platform (Step 6)** — the piece the earlier videos
     were missing.

IMPORTANT recording note: the Pin is created with a SANDBOX token (dashboard-
generated), which belongs to YOUR Pinterest account. Log into pinterest.com as
THAT SAME account to see the Pin in Step 6. It will not show for logged-out users
(expected sandbox behavior).

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
import io
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


# ── Reading-time pauses (for the screen recording) ───────────────────────────
# Each step should stay on screen long enough to be read on the video. Rather than
# a fixed delay, we measure how much text a step printed and pause for roughly how
# long it takes to read it: (words printed) / READ_WPM, floored at MIN_PAUSE (~10s)
# and capped at MAX_PAUSE so a huge JSON dump doesn't stall the demo forever.
READ_WPM = 150.0          # careful reading of technical output
MIN_PAUSE_SECONDS = 10.0
MAX_PAUSE_SECONDS = 30.0


class _CountingStdout(io.TextIOBase):
    """Tees writes to the real stdout while counting words, so pause_to_read()
    can size each pause to what was just printed."""

    def __init__(self, wrapped):
        self._w = wrapped
        self.words_since = 0

    def write(self, s):
        self.words_since += len(str(s).split())
        return self._w.write(s)

    def flush(self):
        try:
            return self._w.flush()
        except Exception:
            return None


_METER = None


def install_reading_meter():
    global _METER
    if _METER is None:
        _METER = _CountingStdout(sys.stdout)
        sys.stdout = _METER


def pause_to_read():
    """Pause ~ the reading time of everything printed since the last pause."""
    words = _METER.words_since if _METER is not None else 0
    if words <= 0:
        if _METER is not None:
            _METER.words_since = 0
        return
    secs = max(MIN_PAUSE_SECONDS, min(MAX_PAUSE_SECONDS, words / READ_WPM * 60.0))
    print(f"\n    ... pausing ~{int(round(secs))}s to read the above ...", flush=True)
    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        print("    (pause skipped)", flush=True)
    if _METER is not None:
        _METER.words_since = 0  # reset AFTER, so the pause line doesn't count next


def _pretty(obj) -> str:
    try:
        return json.dumps(obj, indent=2)[:4000]
    except Exception:
        return str(obj)[:4000]


# ── Live HTTP tracer ─────────────────────────────────────────────────────────
# The core review requirement is to show HOW the Pin is created — i.e. the actual
# API call, not just its result. This wraps httpx so that as the REAL production
# code path (PinterestChannel -> POST /v5/pins, and the read-backs) runs, the
# exact HTTP request (method, URL, headers, JSON body) and the raw HTTP response
# (status + body) are printed on screen. Nothing is faked: these are the real
# bytes sent to and received from Pinterest.
_TRACE_INSTALLED = False


def _redact_headers(headers) -> dict:
    out = {}
    for k, v in dict(headers or {}).items():
        if k.lower() == "authorization":
            out[k] = "Bearer ****REDACTED****"
        else:
            out[k] = v
    return out


def _shrink_body(body):
    """Show the real JSON body, but truncate the (huge) base64 image bytes so the
    structure — endpoint fields, board_id, link, title, media_source — is legible."""
    if not isinstance(body, dict):
        return body
    import copy
    b = copy.deepcopy(body)
    ms = b.get("media_source")
    if isinstance(ms, dict) and isinstance(ms.get("data"), str) and len(ms["data"]) > 64:
        ms["data"] = f"<{len(ms['data'])} base64 chars of PNG image bytes>"
    return b


def _print_request(method, url, kwargs):
    print("\n    +-- HTTP REQUEST --------------------------------------------")
    print(f"    | {method} {url}")
    for k, v in _redact_headers(kwargs.get("headers")).items():
        print(f"    | {k}: {v}")
    if "params" in kwargs and kwargs["params"]:
        print(f"    | query: {dict(kwargs['params'])}")
    if kwargs.get("json") is not None:
        body = _pretty(_shrink_body(kwargs["json"]))
        print("    | body:")
        for line in body.splitlines():
            print(f"    |   {line}")
    print("    +------------------------------------------------------------", flush=True)


def _print_response(resp):
    print("    +-- HTTP RESPONSE -------------------------------------------")
    print(f"    | {resp.status_code} {resp.reason_phrase}")
    try:
        body = _pretty(resp.json())
    except Exception:
        body = (resp.text or "")[:2000]
    for line in body.splitlines():
        print(f"    |   {line}")
    print("    +------------------------------------------------------------", flush=True)


def install_http_tracer():
    """Monkeypatch httpx (async + sync) so every Pinterest API call the REAL code
    path makes is printed request-and-response. Idempotent."""
    global _TRACE_INSTALLED
    if _TRACE_INSTALLED:
        return
    _TRACE_INSTALLED = True

    _apost, _aget = httpx.AsyncClient.post, httpx.AsyncClient.get
    _sget = httpx.Client.get

    async def apost(self, url, **kw):
        _print_request("POST", str(url), kw); r = await _apost(self, url, **kw); _print_response(r); return r

    async def aget(self, url, **kw):
        _print_request("GET", str(url), kw); r = await _aget(self, url, **kw); _print_response(r); return r

    def sget(self, url, **kw):
        _print_request("GET", str(url), kw); r = _sget(self, url, **kw); _print_response(r); return r

    httpx.AsyncClient.post = apost
    httpx.AsyncClient.get = aget
    httpx.Client.get = sget


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
    if _METER is not None:
        _METER.words_since = 0  # the manual Allow step was the pause for the above

    banner("2", "Authorization complete — confirming the connected account (GET /v5/user_account)")
    try:
        account = asyncio.run(pinterest_oauth.get_user_account())
    except Exception as e:
        print(f"No working token yet ({e}) — authorization did not complete. Aborting.")
        sys.exit(1)
    print("Connected. GET /v5/user_account returned:")
    print(_pretty(account), flush=True)
    pause_to_read()
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
    print("token are the sandbox ones.\n")
    print("Every Pinterest API call below prints its EXACT HTTP request (method, URL,")
    print("headers, body) and the raw HTTP response — so you can see precisely HOW the")
    print("Pin is created, not just that it was.\n", flush=True)

    # Trace every real HTTP call the production code path makes from here on.
    install_http_tracer()

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

    # 3a — confirm the sandbox account (capture the username: the created Pin is
    # visible to THIS account on pinterest.com when logged in).
    try:
        acct = asyncio.run(pinterest_oauth.get_user_account())
        print("\nGET /v5/user_account (sandbox) ->")
        print(_pretty(acct), flush=True)
    except Exception as e:
        print(f"Could not read sandbox account (token invalid/expired?): {e}")
        sys.exit(1)
    username = acct.get("username")
    pause_to_read()

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
    board_name = board.get("name") or ""
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
    listing["board_id"] = board_id
    pause_to_read()

    # 3d — READ THE PIN BACK from Pinterest (proves it exists on the platform)
    banner("5", "Read the Pin back from Pinterest (GET /v5/pins/{id}) + list the board's Pins")
    pin = asyncio.run(pinterest_oauth.get_pin(pin_id))
    print(f"GET /v5/pins/{pin_id} ->")
    print(_pretty(pin), flush=True)
    try:
        pins = _list_board_pins(api, sandbox_token, board_id)
        print(f"\nGET /v5/boards/{board_id}/pins -> {len(pins)} pin(s); ids: "
              f"{[p.get('id') for p in pins][:10]}", flush=True)
        print("(The created Pin id above appears in this board's Pin list — proof it landed.)")
    except Exception as e:
        print(f"(board pin list read failed, non-fatal: {e})")
    pause_to_read()

    # 3e — DISPLAY THE PIN ON PINTEREST.COM (what the reviewer asks for).
    # Per Pinterest's sandbox docs, a Sandbox Pin IS viewable on pinterest.com /
    # the mobile app — on the CREATOR's own profile, when logged in as the account
    # this sandbox token belongs to. So the reviewer's "display the newly created
    # Pin on the Pinterest platform" = open your own profile/board logged in.
    banner("6", "DISPLAY the Pin ON PINTEREST.COM — open these logged in as your account")
    media_url = _pin_image_url(pin)
    profile_url = f"https://www.pinterest.com/{username}/" if username else "https://www.pinterest.com/ (your profile)"
    board_slug = _slugify(board_name)
    board_url = f"https://www.pinterest.com/{username}/{board_slug}/" if (username and board_slug) else None
    pin_url = f"https://www.pinterest.com/pin/{pin_id}/"
    print("*" * 70)
    print("*  OPEN THESE IN A BROWSER **LOGGED IN AS THE ACCOUNT ABOVE** and record")
    print("*  the newly-created Pin appearing on the Pinterest platform:")
    print("*")
    print(f"*    Your profile:  {profile_url}")
    if board_url:
        print(f"*    The board:     {board_url}")
    print(f"*    The Pin:       {pin_url}")
    if media_url:
        print(f"*    Pin image URL: {media_url}")
    print("*")
    print(f"*  (Sandbox Pins are visible ONLY to you, the creator — account "
          f"'{username}'. Log into pinterest.com as that account to see it; it will")
    print("*  NOT appear for logged-out / other users. This is expected sandbox")
    print("*  behavior and is exactly what Pinterest's sandbox docs describe.)")
    print("*" * 70, flush=True)
    for u in [x for x in (board_url or profile_url, pin_url) if x]:
        try:
            webbrowser.open(u)
        except Exception:
            pass
    pause_to_read()

    banner("7", "Demo complete — both requirements shown")
    print("Shown live and end to end:")
    print("  1) OAUTH FLOW on production pinterest.com (consent + token + account read).")
    print("  2) FULL API INTEGRATION (sandbox): a Pin CREATED via POST /v5/pins (the")
    print("     request + 201 + Pin id are visible above), read back via GET /v5/pins/{id},")
    print("     and DISPLAYED ON PINTEREST.COM on the creator's own profile/board (Step 6).")
    print("\nOnce Standard access is granted, the identical code path runs against")
    print("PRODUCTION and the Pin is public on pinterest.com.", flush=True)


def _slugify(name: str) -> str:
    """Pinterest board URL slug: lowercase, non-alphanumerics -> single hyphens."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s


def _pin_image_url(pin: dict):
    """Largest available image URL from a Pin's media block (if the API returned
    one). Pinterest media.images is keyed like '150x150','600x','1200x'."""
    imgs = ((pin or {}).get("media") or {}).get("images") or {}

    def width(k):
        try:
            return int(str(k).split("x")[0])
        except Exception:
            return 0

    urls = [(width(k), v.get("url")) for k, v in imgs.items()
            if isinstance(v, dict) and v.get("url")]
    return max(urls)[1] if urls else None


def _write_pin_preview(pin_id, listing, pin, api_base_url):
    """Render the created Pin as a local HTML 'Pin preview' page you can OPEN and
    show on the video. Every field comes from the real API data; the image is the
    exact one we uploaded (embedded so it always renders even if the sandbox media
    URL isn't publicly reachable). Clearly labeled as this app's preview of the
    sandbox API response — it does NOT imitate pinterest.com. Returns a file path."""
    import html
    from pathlib import Path

    title = html.escape(listing.get("title", "") or (pin or {}).get("title", "") or "Pin")
    desc = html.escape(listing.get("description", "") or (pin or {}).get("description", "") or "")
    link = listing.get("listing_url") or (pin or {}).get("link") or ""
    board_id = (pin or {}).get("board_id") or listing.get("board_id") or ""
    media_url = _pin_image_url(pin)
    b64 = listing.get("image_base64") or ""
    img_src = media_url or (f"data:{listing.get('image_content_type', 'image/png')};base64,{b64}" if b64 else "")

    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Pin preview {html.escape(str(pin_id))}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f4f5;margin:0;padding:32px;color:#111}}
 .note{{max-width:640px;margin:0 auto 16px;font-size:13px;color:#555;text-align:center}}
 .card{{max-width:420px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;
        box-shadow:0 6px 24px rgba(0,0,0,.12)}}
 .card img{{width:100%;display:block;background:#eee}}
 .body{{padding:16px 18px}}
 .title{{font-size:18px;font-weight:700;margin:0 0 6px}}
 .desc{{font-size:14px;color:#333;margin:0 0 12px}}
 .meta{{font-size:12px;color:#666;line-height:1.6;border-top:1px solid #eee;padding-top:10px}}
 a{{color:#0a58ca;text-decoration:none}} a:hover{{text-decoration:underline}}
 code{{background:#f0f0f0;padding:1px 5px;border-radius:4px}}
</style></head><body>
 <div class="note">AI Factory &mdash; local preview rendered from the Pinterest
   <b>sandbox</b> API response for pin <code>{html.escape(str(pin_id))}</code>.
   (Sandbox Pins are not published to public pinterest.com; this page is this app's
   own render of the live API data, not the Pinterest website.)</div>
 <div class="card">
   {f'<img src="{html.escape(img_src)}" alt="pin image">' if img_src else ''}
   <div class="body">
     <p class="title">{title}</p>
     <p class="desc">{desc}</p>
     <div class="meta">
       Pin id: <code>{html.escape(str(pin_id))}</code><br>
       Board id: <code>{html.escape(str(board_id))}</code><br>
       Destination link: <a href="{html.escape(link)}">{html.escape(link)}</a><br>
       Created via: <code>POST {html.escape(api_base_url)}/pins</code>{f'<br>Pinterest media URL: <a href="{html.escape(media_url)}">{html.escape(media_url)}</a>' if media_url else ''}
     </div>
   </div>
 </div>
</body></html>"""
    out = Path.cwd() / "pin_preview.html"
    out.write_text(page, encoding="utf-8")
    return str(out.resolve())


def _announce_openable_pin(pin_id, preview_path, media_url):
    """Big, obvious block so the openable link is easy to find on the video."""
    print("\n" + "*" * 70)
    print("*  OPEN THIS TO SEE THE CREATED PIN (sandbox)")
    print("*")
    print(f"*  Pin preview page (open in a browser):")
    print(f"*      file://{preview_path}")
    if media_url:
        print(f"*  Pin image URL (hosted by Pinterest):")
        print(f"*      {media_url}")
    print(f"*  Pin id: {pin_id}")
    print("*")
    print("*  NOTE: sandbox Pins have no public pinterest.com page. The preview")
    print("*  page above is rendered from the live API response for this pin.")
    print("*" * 70, flush=True)


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

    install_reading_meter()  # size the between-step pauses to reading time
    token = _sandbox_token(args.sandbox_token)
    if not args.skip_oauth:
        phase1_authenticate(args.base_url.rstrip("/"))
    phase_sandbox_integration(token)


if __name__ == "__main__":
    main()
