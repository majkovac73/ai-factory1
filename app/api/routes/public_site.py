"""
Public marketing/legal pages for the Pinterest app review (App ID 1587865).

Pinterest requires the app's Main URL and Privacy Policy URL to live on a domain
the developer controls (not GitHub) and the policy to carry specific
Pinterest-API disclosures. These two routes are intentionally OUTSIDE any auth:
they must return 200 HTML to Pinterest's reviewer with no key/session. The
FACTORY_API_KEY middleware only gates mutating methods + /logs, so GET / and
GET /privacy stay public.

Plain server-rendered HTML — no build step, no JS.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

PRODUCT_NAME = "DesignsForAll"
CONTACT_EMAIL = "maj.kovacai@gmail.com"
LAST_UPDATED = "13.07.2026"

_LANDING_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{PRODUCT_NAME}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="font-family: system-ui, sans-serif; max-width: 640px; margin: 60px auto; padding: 0 20px; line-height: 1.6; color: #1a1a1a;">
  <h1>{PRODUCT_NAME}</h1>
  <p>An independent Etsy shop selling digital planners, printables, and
  print-on-demand designs. We use the Pinterest API to publish Pins that promote
  our own product listings.</p>
  <p>Contact: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>
  <p><a href="/privacy">Privacy Policy</a></p>
</body>
</html>"""

_PRIVACY_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Privacy Policy — {PRODUCT_NAME}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="font-family: system-ui, sans-serif; max-width: 720px; margin: 60px auto; padding: 0 20px; line-height: 1.6; color: #1a1a1a;">
  <h1>Privacy Policy — {PRODUCT_NAME}</h1>
  <p><em>Last updated: {LAST_UPDATED}</em></p>

  <p>This Privacy Policy describes how {PRODUCT_NAME} ("we", "us") collects, uses,
  and shares information, including information obtained through the Pinterest API.</p>

  <h2>1. What we collect</h2>
  <p>We use the Pinterest API to publish marketing content (Pins) that promote our
  own product listings, on the account that has explicitly connected and authorized
  this integration. To do this we access that account's basic profile identifier
  and its boards and Pins as needed to create and manage the Pins we publish. We
  only access data for the single Pinterest account that has connected this
  integration, and only after that account grants authorization via Pinterest's
  standard OAuth flow.</p>

  <h2>2. Pinterest API disclosure</h2>
  <p>This service uses the Pinterest API. {PRODUCT_NAME} is not endorsed by,
  affiliated with, or sponsored by Pinterest. "Pinterest" and related marks are
  trademarks of Pinterest, Inc.</p>

  <h2>3. How we use Pinterest data</h2>
  <p>Data obtained via the Pinterest API (such as the account identifier, board
  identifiers, and Pin content and identifiers) is used solely to operate this
  service's own marketing features — publishing our own Pins and managing that
  published content. We do not use Pinterest-derived data for any purpose unrelated
  to operating this service.</p>

  <h2>4. No resale or redistribution</h2>
  <p>We do not sell, rent, or redistribute Pinterest content or Pinterest-derived
  data to any third party. Pinterest data is used internally only, for the purposes
  described above.</p>

  <h2>5. What happens when you disconnect</h2>
  <p>If you disconnect your Pinterest account from this integration, we stop
  accessing your Pinterest data immediately, and any previously stored
  Pinterest-derived data (the stored OAuth access and refresh tokens, and our
  internal records of the Pins we published — their identifiers, links, and
  request payloads) is permanently deleted from our systems immediately.</p>

  <h2>6. Data retention</h2>
  <p>We retain Pinterest-derived data only as long as necessary to operate the
  service described above, or until you disconnect your account, whichever is
  sooner. On disconnect it is deleted immediately as described in Section 5.</p>

  <h2>7. Contact</h2>
  <p>Questions about this policy: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing_page():
    return _LANDING_HTML


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy():
    return _PRIVACY_HTML
