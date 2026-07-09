# Step 100i — Post new listings to Tumblr (not just Pinterest)

**Date:** 2026-07-09

## Problem

Maj noticed a new listing (coloring_page task 56367aba → listing 4535264041) got
**no Tumblr post**. Investigation:

- The post-completion pipeline's social stage (`_stage_pinterest`) is hardcoded to
  `PinterestChannel()` — it **only ever posts to Pinterest**, never Tumblr.
- Tumblr was only posted by the **recurring** `MarketingRefreshWorker` (default
  OFF), which re-promotes *existing* products on a schedule — so a freshly created
  listing was never shared on Tumblr on creation.
- (Also observed: the Pinterest post itself *failed* — "No Pinterest token found";
  Pinterest OAuth isn't connected. Tumblr, by contrast, **is** connected —
  `productsforall` — so it can post.)

## Fix

New `_stage_tumblr` in `PipelineOrchestrator`, called right after `_stage_pinterest`
in step 8. On a new listing it posts to Tumblr using:

- the attractive **watermarked listing mockup** (a catalog `use_case="listing"`
  asset) — **never** the raw deliverable, and
- the real **Etsy listing URL** (`https://www.etsy.com/listing/{listing_id}`).

It is best-effort and skips cleanly when Tumblr isn't configured
(`TUMBLR_CONSUMER_KEY` unset) or not connected (no `TumblrToken`), is idempotent
(skips if this task already has a successful Tumblr post), and never fails the
pipeline. Mirrors how the marketing-refresh worker already builds a Tumblr post.

## Tests

`scripts/test_step100i_tumblr_on_listing.py` (4/4, doubles only): posts with the
watermarked mockup + listing URL when connected; cleanly skips when not connected,
when there's no listing asset, and when already posted (idempotent). Full
regression green (steps 69, 89–96, 98, 100b, 100d, 100f, 100g, 100i).

## Follow-on bug found + fixed during live verify: Tumblr NPF link offsets

The first live run posted to Tumblr but got a **400 Bad Request** ("Hit a snag").
Bisected live against the Tumblr API: the image was fine and plain captions
worked, but the **shop-link `formatting` block** with the emoji-prefixed label
("🛍️ Shop this listing") was rejected. Root cause: NPF inline-formatting
`start`/`end` offsets are counted in **Unicode codepoints, not UTF-16 code
units**. The old `_utf16_len` counted the 🛍 surrogate pair as 2, producing
`start=4,end=21`; Tumblr wants `start=3,end=20` (the emoji = 1 codepoint + the
variation selector + space = 3). Confirmed live: UTF-16 offsets → 400, codepoint
offsets → 201 Created.

Fix: `tumblr_channel._utf16_len` → `_codepoint_len` (just `len(s)`, which counts
codepoints). `scripts/test_tumblr_channel.py` updated to slice the anchor by
codepoint offsets. This also fixes the **marketing-refresh** Tumblr posts, which
share the same caption builder.

## Note

Pinterest still needs its OAuth completed (`/pinterest/oauth/login`) for Pinterest
posts to succeed — that's a separate, user-side connection step, unaffected by this
change.
