# Instructions: Standalone site + privacy policy for Pinterest app review (App ID 1589935)

> AUDIT 2026-07-20 #18: production standardizes on Pinterest **App ID `1589935`**
> ("ai-factory2"). The older `1587865` app referenced historically below is a
> DIFFERENT, superseded app — ignore it; do all review/OAuth/secret work against
> `1589935`. (Occurrences of `1587865` retained only for historical context.)

## Context (read first)

Pinterest rejected the older app (1587865) for two reasons — the same fixes apply
to the current app 1589935:
1. The app's "Main URL" and Privacy Policy URL both point to a GitHub
   repo. Pinterest requires a URL on a domain you actually control (not
   GitHub, not a social profile) — a "standalone website that represents
   your product or company."
2. The privacy policy itself is missing three Pinterest-API-specific
   disclosures (see Part B).

This fix has two independent parts: **Part A** is a Claude Code task
(build and deploy two public pages). **Part B** is Maj's manual task
(edit the app config in Pinterest's developer portal and resubmit) — that
part cannot be done by Claude Code since it requires portal access.

---

## Part A — Claude Code task: add a public site to the existing app

### A1. Add two new, fully public routes to the existing FastAPI app

These must be reachable with NO authentication, NO API key, and NO
session — Pinterest's reviewer will hit them cold. Do not put them behind
any existing auth middleware/prefix.

- `GET /` — a simple landing page identifying the product/company (this
  becomes the "Main URL" submitted to Pinterest)
- `GET /privacy` — the privacy policy (this becomes the "Privacy Policy
  URL")

Plain server-rendered HTML is enough — no build step, no JS framework
needed. Add a new router, e.g. `app/routers/public_site.py`:

```python
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>DesignsForAll</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="font-family: system-ui, sans-serif; max-width: 640px; margin: 60px auto; padding: 0 20px; line-height: 1.6;">
  <h1>DesignsForAll</h1>
  <p>[[ONE_LINE_DESCRIPTION — e.g. "An independent Etsy shop selling digital
  planners, printables, and print-on-demand designs."]]</p>
  <p>Contact: <a href="mailto:maj.kovacai@gmail.com">maj.kovacai@gmail.com</a></p>
  <p><a href="/privacy">Privacy Policy</a></p>
</body>
</html>"""

_PRIVACY_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Privacy Policy — DesignsForAll</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="font-family: system-ui, sans-serif; max-width: 720px; margin: 60px auto; padding: 0 20px; line-height: 1.6;">
  <h1>Privacy Policy — DesignsForAll</h1>
  <p><em>Last updated: 13.07.2026</em></p>

  <!-- FULL POLICY TEXT GOES HERE — see Part B below for the required
       Pinterest-specific paragraphs. Insert the complete policy text
       into this HTML before deploying, don't leave placeholders live. -->

</body>
</html>"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing_page():
    return _LANDING_HTML


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy():
    return _PRIVACY_HTML
```

Register the router in the main FastAPI app instance (wherever other
routers are included, e.g. `app/main.py`):
```python
from app.routers import public_site
app.include_router(public_site.router)
```

**Important:** if `/` is already used for something else (health check,
API root, etc.), don't silently overwrite it — check first, and if it's
taken, mount the landing page at a path like `/about` instead and use
that as the Main URL submitted to Pinterest, adjusting Part C below
accordingly.

### A2. Verify it's really public before touching Pinterest

Locally:
```
curl -i http://localhost:8000/
curl -i http://localhost:8000/privacy
```
Both must return `200` with no auth headers sent.

Deploy to Railway, then verify the same thing against production, e.g.:
```
curl -i https://kind-liberation-production.up.railway.app/
curl -i https://kind-liberation-production.up.railway.app/privacy
```
Both must return real `200` HTML — not a login redirect, not a 404, not
an API JSON error. If either fails, fix it and recheck — don't move to
Part C (Pinterest resubmission) until both are confirmed 200 with real
page content, from the actual production URL, not just localhost.

---

## Part B — Required privacy policy text (insert into `/privacy`)

This is the actual content — insert the full text below into `_PRIVACY_HTML`
in Part A, replacing the placeholder comment. Two clauses below have a
`[[CONFIRM: ...]]` marker — resolve those against the real code before
this goes live; don't publish a legal policy stating something the
system doesn't actually do.

```
This Privacy Policy describes how [[PRODUCT_NAME]] ("we", "us") collects,
uses, and shares information, including information obtained through the
Pinterest API.

1. What we collect
We use the Pinterest API to publish marketing content (Pins) related to
our own product listings on your behalf, and to read basic account and
Pin performance information necessary to do so. We only access data for
the Pinterest account that has explicitly connected/authorized this
integration.

2. Pinterest API disclosure
This service uses the Pinterest API. [[PRODUCT_NAME]] is not endorsed by,
affiliated with, or sponsored by Pinterest. "Pinterest" and related marks
are trademarks of Pinterest, Inc.

3. How we use Pinterest data
Data obtained via the Pinterest API (such as account identifiers, Pin
content, and Pin performance metrics) is used solely to operate this
service's own marketing features — publishing our own Pins and measuring
their performance. We do not use Pinterest-derived data for any purpose
unrelated to operating this service.

4. No resale or redistribution
We do not sell, rent, or redistribute Pinterest content or
Pinterest-derived data to any third party. Pinterest data is used
internally only, for the purposes described above.

5. What happens when you disconnect
[[CONFIRM: state the real behavior. Example if true: "If you disconnect
your Pinterest account, we stop accessing your Pinterest data
immediately, and any previously stored Pinterest-derived data (such as
cached account tokens or Pin performance metrics) is permanently deleted
from our systems within [[N]] days." If the system does NOT currently
delete this data on disconnect, either (a) implement deletion so this is
true, or (b) rewrite this paragraph to accurately describe what actually
happens — do not publish a claim that isn't true.]]

6. Data retention
We retain Pinterest-derived data only as long as necessary to operate the
service described above, or until you disconnect your account, whichever
is sooner.

7. Contact
Questions about this policy: [[CONTACT_EMAIL]]
```

Fill in `[[PRODUCT_NAME]]`, `[[CONTACT_EMAIL]]`, `[[DATE]]`, and resolve
the `[[CONFIRM]]` clause against real disconnect-handling code before
deploying. If no disconnect/token-revocation flow exists in the codebase
yet, flag that back to Maj — the policy shouldn't describe a feature that
doesn't exist.

---

## Part C — What Maj needs to do manually (cannot be done by Claude Code)

1. Confirm the deployed pages are really live and public (Part A2 above).
2. Go to the Pinterest developer portal → the app with ID `1589935` (the current
   production app — NOT the superseded `1587865`).
3. Edit the app's configuration:
   - **App website / Main URL** → the new landing page URL (e.g.
     `https://kind-liberation-production.up.railway.app/` or your chosen
     path)
   - **Privacy Policy URL** → the new `/privacy` URL on the same domain
4. Save, then use the portal's resubmit-for-review action (this is
   typically an "Edit" + "Submit for review" step on the app's page —
   the exact button wording can shift, so just look for the review/submit
   action on the app's settings page after saving the URL changes).
5. If there's a review notes / comments field in the resubmission flow,
   briefly note that both issues from the rejection were addressed: (a)
   Main URL and Privacy Policy are now hosted on your own domain, not
   GitHub, and (b) the privacy policy now includes the Pinterest API
   disclosure, the disconnect/data-deletion statement, and the
   no-resale/no-redistribution statement.
6. Review turnaround varies — Pinterest doesn't publish a fixed SLA, so
   just watch for their response by email/portal notification rather than
   assuming a specific timeframe.

Nothing else in the rejection notice suggests scope/permission changes
are needed — this looks purely like a hosting + policy-content fix.
