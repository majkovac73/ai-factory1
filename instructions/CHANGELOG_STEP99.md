# Step 99 — Tumblr marketing channel + recurring marketing-refresh automation

**Date:** 2026-07-09

---

## Part 1 — Tumblr channel

### API confirmation (fetched current docs, not assumed)

Confirmed against Tumblr's live API docs (github.com/tumblr/docs, July 2026)
and cross-checked against PyTumblr2's working client:

- **OAuth 2.0** (not the old 1.0a). Authorize `https://www.tumblr.com/oauth2/authorize`,
  token `https://api.tumblr.com/v2/oauth2/token`, scope `basic write offline_access`
  (offline_access → refresh_token).
- **Create post:** `POST /v2/blog/{blog-identifier}/posts` as `multipart/form-data`:
  a `json` part holds the NPF body (`content` blocks, `tags` comma-separated,
  `state`), and each image is a form part whose field name equals the
  `identifier` set inside that image block's `media` object.

### Implemented

- `TumblrToken` model (mirrors `PinterestToken`); `tumblr_oauth` service
  (authcode + refresh); `/tumblr/oauth/login` + `/oauth/callback` routes.
- `TumblrChannel(MarketingChannel)` — same `post()` / result shape as
  `PinterestChannel`. Accepts a local image path, base64, or URL; converts a
  PDF delivery asset to a PNG first page (reuses `_delivery_image_bytes`). The
  caption always includes the **listing link** (`🛍️ Shop this: <url>`), or a
  **"🛍️ Link to our Etsy store in bio"** fallback when no link is available.
- Settings: `TUMBLR_CONSUMER_KEY/SECRET/BLOG_NAME`, `TUMBLR_REDIRECT_URI`
  (defaults to the live Railway domain), `TUMBLR_SEND_REDIRECT_URI` (default
  False — see OAuth note below).
- `scripts/test_tumblr_channel.py` — 4/4, doubles only, zero API/generation cost
  (happy path + identifier↔form-part mapping, PDF→PNG, API-failure handling,
  link/fallback caption).

### OAuth setup — confirmed working (with two real bugs fixed during setup)

Maj hit a redirect loop on Tumblr's consent screen. Root causes, fixed:

1. **`redirect_uri_mismatch`**: sending a `redirect_uri` that Tumblr wouldn't
   accept even when registered exactly. Tumblr makes `redirect_uri` optional
   when one callback is registered — we now **omit it** (gated behind
   `TUMBLR_SEND_REDIRECT_URI=False`) and rely on the app's registered default.
2. **The loop itself**: Tumblr delivers the authorization `code` to
   `/tumblr/oauth/login` (that's what the app's callback resolves to), and the
   old handler discarded the code and re-redirected to consent — an infinite
   loop. `/oauth/login` now **completes the token exchange** whenever it
   receives `code`+`state` (and both routes surface `?error=` instead of
   looping). Auth then completed: `{"status":"connected","scope":"basic write
   offline_access"}`.

### Real verification post (zero new generation)

Posted task `127d5130`'s already-generated `hero.png` to Tumblr for real via
`TumblrChannel` — **no image generation**:

- **https://productsforall.tumblr.com/post/821619246282981376** (HTTP 200 live).

Blog: `productsforall`.

---

## Part 2 — Recurring marketing-refresh automation

Re-promotes EXISTING published products using their ALREADY-GENERATED assets,
so it stays cheap however often it runs.

### Design

- **No new `MarketingRefreshState` table.** Checked first (as the spec allowed):
  `MarketingPost` already records `task_id`, `channel`, `status`, `created_at`
  for every post, and refresh posts go through `MarketingService` (which writes
  those rows) — so posting *is* the state update. A separate table would only
  duplicate this and risk drift. Last-marketed is derived from `MarketingPost`.
- **`MarketingRefreshService`**
  - `select_candidates(limit)` — published products (Task `DONE` + recognized
    `product_format` + a real persisted `etsy_listing_id`) whose last successful
    marketing post (any channel) is null or older than
    `MARKETING_REFRESH_MIN_INTERVAL_DAYS`, sorted **least-recently-marketed
    first** (fair rotation; never-marketed first). Blocked tasks are excluded
    for free (they never get a listing_id).
  - `refresh_post(task_id, channel)` — pulls an existing listing/delivery asset
    via `ImageCatalogService` (no regen), optionally makes ONE cheap caption
    rewrite, posts via `MarketingService`/channel, records the `MarketingPost`.
  - `run_cycle(channels, max_posts)` — caps total posts at
    `MARKETING_REFRESH_MAX_POSTS_PER_CYCLE`.
- **`MarketingRefreshWorker`** — same pattern as `AutonomyWorker` (heartbeat,
  death-alert, kill switch). Started/stopped in `app/main.py`; added to the
  stale-heartbeat health check.
- **Settings (all default OFF/conservative, Maj enables explicitly):**
  `MARKETING_REFRESH_ENABLED=False`, `MARKETING_REFRESH_SCHEDULE_SECONDS=21600`
  (6h), `MARKETING_REFRESH_MIN_INTERVAL_DAYS=7`,
  `MARKETING_REFRESH_MAX_POSTS_PER_CYCLE=3`.

### Two fixes this required (both pre-existing)

1. **Publish now persists `listing_id`.** The digital pipeline created a real
   listing but never stored its id anywhere queryable (`output_data` had only
   content; `ImageAsset.listing_id` was empty). On readback-verified publish,
   `_stage_attach_publish` now calls `ImageCatalogService.attach_listing` for
   the delivery + listing assets — the durable "published product" signal
   `select_candidates` needs.
2. **`MarketingService.post_to_channel` detached-session bug.** It accessed
   `record.id` after closing the session → `DetachedInstanceError` ("Instance
   <MarketingPost> is not bound to a Session") — the exact error that broke the
   Pinterest marketing step in step 98. Fixed by capturing the id while bound.
   This fixes ongoing Pinterest marketing too.

### Tests

`scripts/test_marketing_refresh.py` — 4/4, doubles only, zero cost: oldest-first
selection; within-interval + unpublished exclusion; `MAX_POSTS_PER_CYCLE`
respected with existing assets + listing links + recording; and
`MARKETING_REFRESH_ENABLED=False` keeps the worker fully inert.

### Real end-to-end demonstration (zero new generation)

Ran a real `MarketingRefreshService.refresh_post` for the published Mindfulness
product (`b35b4ba9`, real listing `4534803479`): resolved the listing_id, pulled
the existing `hero.png`, rewrote the caption via one LLM call, posted to Tumblr:

- **https://productsforall.tumblr.com/post/821657812106084352** (HTTP 200 live).
- Confirmed by reading the post back — caption includes the listing link:
  `🛍️ Shop this: https://www.etsy.com/listing/4534803479`, tags applied.

---

## Cost

- **Tumblr posting: $0 per post** (free API). **No image generation** anywhere
  in this feature — refresh reuses on-disk assets.
- **Optional caption rewrite: ~$0.00006 per post** (one `openai/gpt-4o-mini`
  call, ~90 in / ~70 out tokens). At the default cap (3 posts/cycle × 4
  cycles/day = 12 posts/day) that's **~$0.0007/day** — negligible against
  `MAX_DAILY_SPEND_USD=5.00`. Even 10× the cadence stays under a cent a day.
  The rewrite can be disabled (`rewrite_caption=False`) for exactly $0.

## Safety

`MARKETING_REFRESH_ENABLED` stays **False** — confirmed active as a kill switch
in production (`MarketingRefreshWorker: started — MARKETING_REFRESH_ENABLED=False
(kill switch active, no posts)`). No recurring posting happens until Maj sets it
True deliberately, same as `AUTONOMY_ENABLED` / `AUTO_PUBLISH_LISTINGS`.
