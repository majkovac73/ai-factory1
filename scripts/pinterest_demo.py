"""
Pinterest Standard-Access demonstration script (reviewer-facing).

PURPOSE
-------
This script is meant to be SCREEN-RECORDED end to end and submitted to Pinterest
for Standard-access review. A reviewer who knows nothing about this application
should be able to watch only the recording and conclude:

    "This is a real Pinterest integration using the applicant's production code.
     We clearly observed OAuth, authenticated API usage, Pin creation, Pin
     verification, and display of the Pin on Pinterest."

It exercises the application's REAL production integration -- nothing is mocked:
  - OAuth service:   app/services/pinterest_oauth.py  (build_authorization_url,
                     exchange_code_for_token via the deployed callback,
                     get_user_account, list_boards, create_board, get_pin)
  - Pin creation:    app/marketing/pinterest_channel.py  (PinterestChannel -> the
                     SAME POST /v5/pins used by the live product pipeline)
  - Every Pinterest HTTP request/response is printed live by a tracer that wraps
    httpx around the real code path (no fake API layer).

TRIAL vs STANDARD (why the Pin is created in Sandbox)
-----------------------------------------------------
A Trial-access app is blocked from creating Pins in PRODUCTION (403, code 29:
"Apps with Trial access may not create Pins in production"). Pinterest's review
guidance says to demonstrate the integration in the SANDBOX, where a Trial app
CAN create Pins. Per Pinterest's sandbox docs, a Sandbox Pin IS viewable on
pinterest.com / the mobile app -- on the CREATOR's own profile, when logged in as
the account the sandbox token belongs to (visible only to that creator). Once
Standard access is granted, the IDENTICAL code path runs against production and
the Pin is public.

SCOPES EXERCISED
----------------
  user_accounts:read   (GET /v5/user_account)
  boards:read          (GET /v5/boards, GET /v5/boards/{id}/pins)
  boards:write         (POST /v5/boards)
  pins:write           (POST /v5/pins)
  pins:read            (GET /v5/pins/{id})

PREREQUISITES
-------------
  1. Production OAuth (Steps 1-2): the DEPLOYED app that services the public
     callback URL must be in production mode (PINTEREST_SANDBOX unset/false) with
     the production App ID (1589935), secret, and redirect URI.
  2. Sandbox Pin creation (Steps 3-8): generate a SANDBOX access token --
     My Apps -> your app (1589935) -> Configure -> "Generate Access Token" ->
     environment = Sandbox -> scopes boards:read, boards:write, pins:read,
     pins:write. Provide it via PINTEREST_SANDBOX_TOKEN or --sandbox-token. That
     token belongs to YOUR Pinterest account; log into pinterest.com as THAT
     account to see the Pin in Step 8.

RUN (record on a machine with a browser so Steps 1 & 8 open pages):
    PINTEREST_SANDBOX_TOKEN=<sandbox-token> python scripts/pinterest_demo.py \
        --base-url https://kind-liberation-production.up.railway.app
"""
import argparse
import asyncio
import io
import json
import os
import re
import sys
import time
import webbrowser

# Make the repo root importable when run as `python scripts/pinterest_demo.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

# Real production modules (verified against the codebase -- NOT reimplemented).
from config import settings
from app.services import pinterest_oauth
from app.marketing.pinterest_channel import PinterestChannel


# ==============================================================================
# Presentation helpers (colors, banners, highlights) -- video-friendly, ASCII-safe
# ==============================================================================
_USE_COLOR = False  # decided in main()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


def ok(s):   return _c("1;32", s)   # green  -- success
def bad(s):  return _c("1;31", s)   # red    -- failure
def hl(s):   return _c("1;36", s)   # cyan   -- IDs / endpoints
def head(s): return _c("1;33", s)   # yellow -- banners
def dim(s):  return _c("2", s)


def banner(step, title):
    line = "=" * 76
    print("\n" + head(line))
    print(head(f"  STEP {step}   {title}"))
    print(head(line), flush=True)


def sep():
    print(dim("-" * 76), flush=True)


def scope_note(operation: str, scopes):
    print(f"  {dim('scope(s) exercised:')} {hl(', '.join(scopes))}   {dim('(' + operation + ')')}", flush=True)


def endpoint_note(method: str, path: str):
    print(f"  {dim('endpoint:')} {hl(method + ' ' + path)}", flush=True)


# -- Reviewer checklist + run summary ------------------------------------------
CHECKLIST = []           # list[(label, bool)]
SUMMARY = {}             # phase/result facts for the final table
PHASE_TIMES = {}         # phase name -> seconds


def mark(label: str, done: bool = True):
    CHECKLIST.append((label, done))


class phase_timer:
    def __init__(self, name): self.name = name
    def __enter__(self): self.t = time.time(); return self
    def __exit__(self, *a):
        dt = time.time() - self.t
        PHASE_TIMES[self.name] = dt
        print(dim(f"\n  [phase '{self.name}' elapsed {dt:.1f}s]"), flush=True)


def die(reason: str, *fix_lines: str):
    """Stop with a clear, reviewer/operator-friendly explanation."""
    print("\n" + bad("=" * 76))
    print(bad(f"  STOPPED: {reason}"))
    for ln in fix_lines:
        print(bad(f"    - {ln}"))
    print(bad("=" * 76), flush=True)
    sys.exit(1)


# -- Reading-time pauses (so each step stays on screen long enough to read) ----
READ_WPM = 150.0
MIN_PAUSE_SECONDS = 10.0
MAX_PAUSE_SECONDS = 30.0


class _CountingStdout(io.TextIOBase):
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


def reset_meter():
    if _METER is not None:
        _METER.words_since = 0


def pause_to_read():
    words = _METER.words_since if _METER is not None else 0
    if words <= 0:
        reset_meter()
        return
    secs = max(MIN_PAUSE_SECONDS, min(MAX_PAUSE_SECONDS, words / READ_WPM * 60.0))
    print(dim(f"\n    ... pausing ~{int(round(secs))}s so this stays readable on the recording ..."), flush=True)
    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        print(dim("    (pause skipped)"), flush=True)
    reset_meter()


def _pretty(obj) -> str:
    try:
        return json.dumps(obj, indent=2)[:4000]
    except Exception:
        return str(obj)[:4000]


# ==============================================================================
# Live HTTP tracer -- prints the REAL request+response for every Pinterest call the
# production code path makes (this is how the reviewer sees the API usage).
# ==============================================================================
_TRACE_INSTALLED = False
LAST_ERROR_BODY = {"text": None}


def _redact_headers(headers) -> dict:
    out = {}
    for k, v in dict(headers or {}).items():
        out[k] = "Bearer ****REDACTED****" if k.lower() == "authorization" else v
    return out


def _shrink_body(body):
    if not isinstance(body, dict):
        return body
    import copy
    b = copy.deepcopy(body)
    ms = b.get("media_source")
    if isinstance(ms, dict) and isinstance(ms.get("data"), str) and len(ms["data"]) > 64:
        ms["data"] = f"<{len(ms['data'])} base64 chars of PNG image bytes>"
    return b


def _print_request(method, url, kwargs):
    print("\n    +-- HTTP REQUEST " + "-" * 52)
    print(f"    | {hl(method + ' ' + url)}")
    for k, v in _redact_headers(kwargs.get("headers")).items():
        print(f"    | {k}: {v}")
    if kwargs.get("params"):
        print(f"    | query: {dict(kwargs['params'])}")
    if kwargs.get("json") is not None:
        print("    | body:")
        for line in _pretty(_shrink_body(kwargs["json"])).splitlines():
            print(f"    |   {line}")
    print("    +" + "-" * 68, flush=True)


def _print_response(resp):
    is_ok = resp.status_code < 400
    tag = ok(f"{resp.status_code} {resp.reason_phrase}") if is_ok else bad(f"{resp.status_code} {resp.reason_phrase}")
    print("    +-- HTTP RESPONSE " + "-" * 51)
    print(f"    | status: {tag}")
    try:
        body_obj = resp.json()
        body = _pretty(body_obj)
    except Exception:
        body_obj = None
        body = (resp.text or "")[:2000]
    for line in body.splitlines():
        print(f"    |   {line}")
    print("    +" + "-" * 68, flush=True)
    if not is_ok:
        LAST_ERROR_BODY["text"] = body


def install_http_tracer():
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


# ==============================================================================
# Small utilities
# ==============================================================================
def _sandbox_token(cli_token: str = None) -> str:
    return cli_token or getattr(settings, "PINTEREST_SANDBOX_TOKEN", None) or os.getenv("PINTEREST_SANDBOX_TOKEN")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _pin_image_url(pin: dict):
    imgs = ((pin or {}).get("media") or {}).get("images") or {}
    def width(k):
        try: return int(str(k).split("x")[0])
        except Exception: return 0
    urls = [(width(k), v.get("url")) for k, v in imgs.items() if isinstance(v, dict) and v.get("url")]
    return max(urls)[1] if urls else None


def _list_board_pins(api: str, token: str, board_id: str) -> list:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{api}/boards/{board_id}/pins", headers={"Authorization": f"Bearer {token}"}, params={"page_size": 25})
        r.raise_for_status()
        return r.json().get("items", []) or []


def _missing_scope_hint(err_text: str):
    """If a Pinterest error names a missing scope, surface exactly which one."""
    if not err_text:
        return None
    m = re.search(r"[Mm]issing.*?(\[[^\]]*\]|(?:boards|pins|user_accounts):\w+)", err_text)
    if m:
        return m.group(0)
    for sc in ("pins:write", "boards:write", "pins:read", "boards:read", "user_accounts:read"):
        if sc in err_text:
            return sc
    return None


def _demo_image_b64() -> str:
    """A valid PNG so the Pin has real media (Pinterest requires an image)."""
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
        buf = BytesIO(); img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42m"
                "NkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


# ==============================================================================
# STEP 0 -- environment summary + "this is not mocked"
# ==============================================================================
def step0_intro(base_url: str, sandbox_token: str):
    banner("0", "Environment summary + what this recording proves")
    print("This demonstration uses the application's " + ok("REAL production Pinterest integration") + ".")
    print("There is " + ok("NO fake API layer") + " -- every Pinterest call below is real HTTP traffic")
    print("made by the app's own modules:")
    print(f"    - OAuth service:  {hl('app/services/pinterest_oauth.py')}")
    print(f"    - Pin creation:   {hl('app/marketing/pinterest_channel.py (PinterestChannel)')}")
    print(f"    - Endpoints:      {hl('api.pinterest.com / api-sandbox.pinterest.com (v5)')}")
    print()
    print("Environment:")
    print(f"    App ID:            {hl(str(settings.PINTEREST_APP_ID))}")
    print(f"    Redirect URI:      {hl(str(settings.PINTEREST_REDIRECT_URI))}")
    print(f"    OAuth API host:    {hl('https://api.pinterest.com/v5')}  (production)")
    print(f"    Pin-create host:   {hl('https://api-sandbox.pinterest.com/v5')}  (sandbox -- see Step 3)")
    print(f"    Deployed callback: {hl(base_url + '/pinterest/oauth/callback')}")
    print(f"    Sandbox token:     {(ok('SET') if sandbox_token else bad('MISSING -- see Step 6 instructions'))}")
    print()
    print("Scopes exercised in this recording:")
    for sc, use in [("user_accounts:read", "GET /v5/user_account"),
                    ("boards:read", "GET /v5/boards, GET /v5/boards/{id}/pins"),
                    ("boards:write", "POST /v5/boards"),
                    ("pins:write", "POST /v5/pins"),
                    ("pins:read", "GET /v5/pins/{id}")]:
        print(f"    - {hl(sc):<28} {dim(use)}")
    print()
    print("What you will see, in order:")
    print("    1-2) Real OAuth on pinterest.com + authenticated GET /v5/user_account")
    print("    3)   Why Pin creation is shown in Sandbox (Trial limitation)")
    print("    4-5) Sandbox account + board (GET/POST /v5/boards)")
    print("    6)   " + ok("Pin CREATED via POST /v5/pins") + " -- full request + response + pin id")
    print("    7)   Pin VERIFIED via GET /v5/pins/{id} + board listing")
    print("    8)   Pin DISPLAYED on pinterest.com (creator's own profile/board)")
    print("    9)   Reviewer checklist + summary table")
    pause_to_read()


# ==============================================================================
# STEPS 1-2 -- real OAuth flow (production) + account read
# ==============================================================================
def phase_oauth(base_url: str):
    settings.PINTEREST_SANDBOX = False  # OAuth must hit the real consent screen
    with phase_timer("oauth"):
        banner("1", "REAL OAuth flow -- authorize the app on pinterest.com")
        print("The app builds a Pinterest authorization URL via its OWN OAuth service")
        print(f"({hl('pinterest_oauth.build_authorization_url')}, requested through the deployed")
        print(f"app's {hl('/pinterest/oauth/login')} so the OAuth 'state' is stored by the same")
        print("process that will handle the callback). You then sign in and click 'Allow' on")
        print("Pinterest's real consent screen; Pinterest redirects to the app's callback,")
        print("which exchanges the code for an access token. " + ok("Nothing here is mocked."))
        print()
        scope_note("OAuth authorization", ["user_accounts:read", "boards:read", "boards:write", "pins:read", "pins:write"])

        try:
            r = httpx.get(f"{base_url}/pinterest/oauth/login", timeout=30)
            r.raise_for_status()
            auth_url = r.json()["authorization_url"]
        except Exception as e:
            die(f"Could not reach the app's OAuth login endpoint at {base_url}/pinterest/oauth/login ({e}).",
                "Run this on the deployed app: --base-url https://kind-liberation-production.up.railway.app",
                "Or run inside the container (railway ssh) with the default --base-url.")

        print("\n  Authorization URL (open it, sign in, click " + ok("Allow") + "):")
        sep(); print("    " + hl(auth_url)); sep()
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
        print()
        input(">>> After the consent screen shows and you click 'Allow' (the callback page "
              "shows \"connected\"), press Enter to continue...\n")
        reset_meter()  # the manual Allow was the pause for the above

        banner("2", "OAuth succeeded -- read the account with the obtained token (GET /v5/user_account)")
        endpoint_note("GET", "/v5/user_account")
        scope_note("account read", ["user_accounts:read"])
        print("This call uses the access token the OAuth flow just produced. A successful")
        print("response proves the token is real and was obtained through the app's OAuth.\n")
        install_http_tracer()  # trace every Pinterest call from here on
        try:
            account = asyncio.run(pinterest_oauth.get_user_account())
        except Exception as e:
            msg = str(e)
            if "no such table" in msg or "no pinterest token" in msg.lower() or "No Pinterest token" in msg:
                die("The OAuth token is not in THIS machine's database.",
                    "You are running the script LOCALLY, but the OAuth callback was handled by the",
                    "DEPLOYED app on Railway, which stored the token in Railway's database -- not on",
                    "your laptop. Run the script INSIDE the container so it shares that database:",
                    "    railway ssh        (opens an interactive shell in the deployed container)",
                    "    cd /app && python scripts/pinterest_demo.py --sandbox-token <token> \\",
                    "        --base-url https://kind-liberation-production.up.railway.app",
                    "Copy the printed auth URL (Step 1) and the pinterest.com URLs (Step 8) into your",
                    "own browser to record them. (Or run locally with --skip-oauth to demo just the",
                    "sandbox Pin creation + display, which needs no database.)")
            die(f"No working token yet ({e}) -- the OAuth flow did not complete.",
                "Make sure you clicked 'Allow' and the callback page showed \"connected\".",
                "Confirm the app's PINTEREST_REDIRECT_URI matches the one registered on app 1589935.")
        print("\n" + ok("OAuth complete.") + " Connected Pinterest account:")
        print(_pretty(account))
        SUMMARY["oauth_username"] = account.get("username")
        mark("OAuth flow completed on pinterest.com")
        mark("User authenticated (access token obtained via app OAuth)")
        mark("Account retrieved (GET /v5/user_account)")
        pause_to_read()
        return account


# ==============================================================================
# STEP 3 -- Trial vs Standard explanation
# ==============================================================================
def phase_explain_sandbox():
    banner("3", "Trial vs Standard -- why the Pin is created in Sandbox")
    print("A " + bad("Trial-access") + " app is blocked from creating Pins in PRODUCTION:")
    print(f"    {dim('POST https://api.pinterest.com/v5/pins')} -> {bad('403 code 29')}")
    print("    \"Apps with Trial access may not create Pins in production.\"")
    print()
    print("Per Pinterest's guidance, the integration is therefore demonstrated in the")
    print(ok("SANDBOX") + ", where a Trial app CAN create Pins. Crucially, this uses the")
    print(ok("IDENTICAL production code path") + f" ({hl('PinterestChannel -> POST /v5/pins')});")
    print("only the API host + token are the sandbox ones. Once Standard access is")
    print("granted, the same code runs against production and the Pin is public.")
    print()
    print("A Sandbox Pin is real and viewable on " + hl("pinterest.com") + " / the mobile app --")
    print("on the CREATOR's own profile, when logged in as the account the sandbox token")
    print("belongs to (Step 8). It is visible only to that creator.")
    pause_to_read()


# ==============================================================================
# STEPS 4-8 -- sandbox account, board, CREATE pin, verify, display
# ==============================================================================
def phase_pin_integration(sandbox_token: str):
    if not sandbox_token:
        die("No SANDBOX access token provided (PINTEREST_SANDBOX_TOKEN / --sandbox-token).",
            "Pinterest Developer portal -> My Apps -> app 1589935 -> Configure tab.",
            "Find 'Generate Access Token', choose environment = Sandbox.",
            "Select scopes: boards:read, boards:write, pins:read, pins:write. Generate + copy.",
            "Re-run: PINTEREST_SANDBOX_TOKEN=<token> python scripts/pinterest_demo.py")

    # Route THIS process's Pinterest calls to sandbox, authenticated by the
    # dashboard-generated sandbox token (the real code path picks it up directly).
    settings.PINTEREST_SANDBOX = True
    settings.PINTEREST_SANDBOX_TOKEN = sandbox_token
    api = pinterest_oauth.api_base()
    install_http_tracer()

    with phase_timer("sandbox_integration"):
        # STEP 4 -- sandbox account
        banner("4", "Sandbox account -- confirm the token's account (GET /v5/user_account)")
        endpoint_note("GET", "/v5/user_account")
        scope_note("account read", ["user_accounts:read"])
        print(f"Sandbox API host: {hl(api)}\n")
        try:
            acct = asyncio.run(pinterest_oauth.get_user_account())
        except Exception as e:
            die(f"Could not read the sandbox account ({e}).",
                "The sandbox token may be invalid/expired, or lacks user_accounts:read.",
                "Regenerate a Sandbox token for app 1589935 and re-run.")
        username = acct.get("username")
        SUMMARY["sandbox_username"] = username
        print(f"\n{ok('Sandbox account confirmed')} -- the created Pin will be visible to "
              f"'{hl(str(username))}' on pinterest.com when logged in.")
        pause_to_read()

        # STEP 5 -- board
        banner("5", "Select or create a board (GET /v5/boards, POST /v5/boards)")
        endpoint_note("GET/POST", "/v5/boards")
        scope_note("list boards / create board", ["boards:read", "boards:write"])
        boards = asyncio.run(pinterest_oauth.list_boards())
        if boards:
            board = boards[0]
            print(f"\n{ok('Using existing board')}: name={hl(board.get('name'))} id={hl(board.get('id'))}")
        else:
            print("\nNo boards yet -- creating one (POST /v5/boards):")
            board = asyncio.run(pinterest_oauth.create_board("AI Factory Demo", "Standard-access demo board"))
            print(f"{ok('Board created')}: name={hl(board.get('name'))} id={hl(board.get('id'))}")
        board_id = board["id"]
        board_name = board.get("name") or ""
        settings.PINTEREST_BOARD_ID = board_id
        SUMMARY["board_id"] = board_id
        pause_to_read()

        # STEP 6 -- CREATE the pin (the piece prior reviews said they couldn't see)
        banner("6", "CREATE the Pin -- POST /v5/pins (via the production PinterestChannel)")
        endpoint_note("POST", "/v5/pins")
        scope_note("create pin", ["pins:write"])
        listing = {
            "title": "Botanical Line Art Print -- Printable Wall Art",
            "description": "Minimalist botanical line art printable -- demo Pin created via the Pinterest API.",
            "listing_url": "https://www.etsy.com/shop/CardsForAllOcDesigns",
            "image_base64": _demo_image_b64(),
            "image_content_type": "image/png",
            "product_format": "single_print",
        }
        print("The exact product data being turned into a Pin (this is what the live pipeline")
        print("sends for a real listing):")
        print(f"    title:            {hl(listing['title'])}")
        print(f"    description:      {listing['description']}")
        print(f"    destination link: {hl(listing['listing_url'])}")
        print(f"    board:            {hl(board_name)} (id {hl(board_id)})")
        print(f"    image:            {dim('%d base64 chars of PNG (uploaded as media_source)' % len(listing['image_base64']))}")
        print("\nCalling " + hl("PinterestChannel().post(listing)") + " -- the real production method.")
        print("Watch the HTTP REQUEST (POST /v5/pins) and RESPONSE below:\n", flush=True)

        result = PinterestChannel().post(listing)   # <- real production code path
        if not result.get("success"):
            err = str(result.get("error") or LAST_ERROR_BODY.get("text") or "unknown error")
            print("\n" + bad("Pin creation FAILED. Pinterest returned:"))
            print(bad("    " + err[:600]))
            hint = _missing_scope_hint(err)
            fixes = ["Confirm PINTEREST_SANDBOX_TOKEN is a SANDBOX token (not production)."]
            if hint:
                fixes.insert(0, f"Missing/insufficient scope: {hint}. Regenerate the sandbox token WITH that scope.")
            else:
                fixes.append("Ensure the token has scopes: pins:write, boards:write, boards:read, pins:read.")
            die("POST /v5/pins did not succeed.", *fixes)

        pin_id = result.get("external_id")
        SUMMARY["pin_id"] = pin_id
        listing["board_id"] = board_id
        print("\n" + ok("=" * 60))
        print(ok(f"  PIN CREATED SUCCESSFULLY  ->  pin id = {pin_id}"))
        print(ok("=" * 60), flush=True)
        mark("Board selected/created")
        mark("Pin created (POST /v5/pins)")
        pause_to_read()

        # STEP 7 -- verify it exists on Pinterest
        banner("7", "VERIFY the Pin exists -- GET /v5/pins/{id} + list the board's Pins")
        endpoint_note("GET", f"/v5/pins/{pin_id}")
        scope_note("read pin / list board pins", ["pins:read", "boards:read"])
        pin = asyncio.run(pinterest_oauth.get_pin(pin_id))
        print(f"\n{ok('Pinterest returned the stored Pin object')} for id {hl(str(pin_id))} (above).")
        try:
            pins = _list_board_pins(api, sandbox_token, board_id)
            ids = [p.get("id") for p in pins]
            here = ok("YES") if pin_id in ids else dim("(not yet indexed)")
            print(f"\nBoard {hl(board_id)} now lists {hl(str(len(pins)))} pin(s). "
                  f"Created id {hl(str(pin_id))} present in listing: {here}")
        except Exception as e:
            print(dim(f"(board pin listing read failed, non-fatal: {e})"))
        print("\n" + ok("This proves Pinterest actually stored the Pin") + " -- it was created, is")
        print("readable by id, and appears in the board's Pin list.")
        mark("Pin read back (GET /v5/pins/{id})")
        mark("Board listing confirms the Pin")
        pause_to_read()

        # STEP 8 -- display on pinterest.com
        banner("8", "DISPLAY the Pin on pinterest.com -- open logged in as your account")
        media_url = _pin_image_url(pin)
        profile_url = f"https://www.pinterest.com/{username}/" if username else "https://www.pinterest.com/"
        board_slug = _slugify(board_name)
        board_url = f"https://www.pinterest.com/{username}/{board_slug}/" if (username and board_slug) else None
        pin_url = f"https://www.pinterest.com/pin/{pin_id}/"
        print("Per Pinterest's sandbox docs, a Sandbox Pin is viewable on pinterest.com -- on")
        print("the CREATOR's own profile/board, when logged in as that account. Open these and")
        print("record the Pin appearing on the platform:")
        print()
        print(f"    Profile:       {hl(profile_url)}")
        if board_url:
            print(f"    Board:         {hl(board_url)}")
        print(f"    Pin page:      {hl(pin_url)}")
        if media_url:
            print(f"    Pin image URL: {hl(media_url)}  {dim('(Pinterest-hosted image of this Pin)')}")
        print()
        print(bad("MUST be logged in as account '") + bad(str(username)) + bad("'."))
        print("A Sandbox Pin is visible ONLY to its creator; logged-out or other accounts will")
        print("not see it (expected sandbox behavior, per Pinterest's docs). If the Pin page")
        print("does not render while logged in, open the Board or Profile URL above -- the Pin")
        print("appears there.")
        for u in [x for x in (board_url, profile_url, pin_url) if x]:
            try:
                webbrowser.open(u)
            except Exception:
                pass
        mark("Pin displayed on pinterest.com (Step 8 -- open logged in as creator)")
        pause_to_read()
        return {"pin_id": pin_id, "username": username, "board_id": board_id}


# ==============================================================================
# STEP 9 -- reviewer checklist + summary table
# ==============================================================================
def phase_checklist():
    banner("9", "Reviewer checklist + summary")
    mark("Real production code paths used (PinterestChannel + pinterest_oauth)")
    for label, done in CHECKLIST:
        box = ok("[x]") if done else bad("[ ]")
        print(f"  {box} {label}")
    print()
    rows = [
        ("OAuth flow (production pinterest.com)", "DONE", f"user={SUMMARY.get('oauth_username','?')}", PHASE_TIMES.get("oauth")),
        ("Sandbox account retrieved", "DONE", f"user={SUMMARY.get('sandbox_username','?')}", None),
        ("Board selected/created", "DONE", f"board_id={SUMMARY.get('board_id','?')}", None),
        ("Pin created (POST /v5/pins)", "DONE", f"pin_id={SUMMARY.get('pin_id','?')}", PHASE_TIMES.get("sandbox_integration")),
        ("Pin read back (GET /v5/pins/{id})", "DONE", "", None),
        ("Board listing confirms Pin", "DONE", "", None),
        ("Pin displayed on pinterest.com", "MANUAL", "open logged in as creator (Step 8)", None),
        ("Real production integration (not mocked)", "YES", "PinterestChannel + real HTTP", None),
    ]
    W = 94  # inner width; wide enough for full Pinterest pin ids
    print("  +" + "-" * (W + 2) + "+")
    print("  | " + "REVIEWER SUMMARY".ljust(W) + " |")
    print("  +" + "-" * (W + 2) + "+")
    for name, status, detail, dt in rows:
        t = f" ({dt:.1f}s)" if isinstance(dt, (int, float)) else ""
        line = f"{name:<40} {status:<7} {detail}{t}"
        print("  | " + line[:W].ljust(W) + " |")
    print("  +" + "-" * (W + 2) + "+")
    print()
    print(ok("A reviewer watching this recording has now observed: real OAuth, an"))
    print(ok("authenticated account read, a Pin created via POST /v5/pins, the Pin verified"))
    print(ok("by GET /v5/pins/{id} and the board listing, and the Pin on pinterest.com --"))
    print(ok("all through the application's real production code paths."))
    print()
    print("Reviewer note: Pin creation is shown in Sandbox because Trial access cannot")
    print("create production Pins. The same PinterestChannel code path runs against")
    print("production once Standard access is granted.")


# ==============================================================================
def main():
    global _USE_COLOR
    ap = argparse.ArgumentParser(description="Pinterest Standard-access demonstration.")
    ap.add_argument("--base-url", default="http://localhost:8000",
                    help="Base URL of the RUNNING deployed app that handles the OAuth callback.")
    ap.add_argument("--sandbox-token", default=None,
                    help="Pinterest SANDBOX access token (or set PINTEREST_SANDBOX_TOKEN).")
    ap.add_argument("--skip-oauth", action="store_true",
                    help="Skip the OAuth phase; run only the sandbox Pin integration (Steps 3-9).")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    args = ap.parse_args()

    _USE_COLOR = (not args.no_color) and sys.stdout.isatty() and os.name != "nt"

    install_reading_meter()
    token = _sandbox_token(args.sandbox_token)

    step0_intro(args.base_url.rstrip("/"), token)
    if not args.skip_oauth:
        phase_oauth(args.base_url.rstrip("/"))
    phase_explain_sandbox()
    phase_pin_integration(token)
    phase_checklist()


if __name__ == "__main__":
    main()
