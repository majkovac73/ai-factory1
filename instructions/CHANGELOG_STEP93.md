
## Step 93 — Digital-file-in-editor investigation (file was fine) + real taxonomy fix (was actually broken)
**Date:** 2026-07-08

---

### Part 1 — "digital file not visible in Etsy's editor UI"

Investigated with real, live API calls against listing 4534427807
(read-only, per instructions — no writes made as part of Part 1):

1. `ImageCatalogService.get_delivery_asset()`: real asset existed
   (confirmed in step 92 already).
2. Digital-file upload call: confirmed called, `POST .../files` returned
   `201 Created` (confirmed in step 92 already).
3. **`GET /v3/application/listings/4534427807` real response:
   `"listing_type": "download"`.** This is correct — the listing-level
   type field (returned as `listing_type` in the response, sent as `type`
   in the request — an asymmetric field name worth remembering) genuinely
   matches what `create_draft_listing()` sent. **Part 1's hypothesis (wrong
   listing type) is disproven.**
4. **The real explanation: `"taxonomy_id": 1`.** Node 1 in Etsy's live
   seller-taxonomy tree (`GET /v3/application/seller-taxonomy/nodes`,
   fetched live) is **"Accessories"** — a physical-goods category (belts,
   hats, gloves, etc.). Etsy's listing editor most likely doesn't surface
   the "Digital files" section for a listing filed under a physical-goods
   category, regardless of `listing_type`. This is the same underlying bug
   Part 2 investigates — not a separate "type" field bug.

**No fix needed for Part 1 specifically** — fixing the taxonomy (Part 2)
is the fix.

---

### Part 2 — real taxonomy tree, real leaf mapping, fix going forward, fix existing listings

**Step A — real tree, not guessed IDs.** Called Etsy's live
`getSellerTaxonomyNodes` (`GET /v3/application/seller-taxonomy/nodes`).
15 top-level categories, 3065 nodes total when flattened. **Confirmed node
1 = "Accessories"** — exactly the category Etsy's editor flagged as too
broad, and the value `EtsyClient.create_draft_listing()` had been silently
defaulting to (`listing.get("taxonomy_id", 1)`) because **nothing upstream
had ever set taxonomy_id at all** — `ListingGeneratorAgent` only ever
produces a free-text `category` string, never a `taxonomy_id` integer.

**Step B — real leaf mapping** (verified each has 0 children, i.e. is a
genuine leaf, except two noted below):

| product_format | taxonomy_id | name | path |
|---|---|---|---|
| `single_print`, `phone_wallpaper` | 2078 | Digital Prints | Art & Collectibles > Prints > Digital Prints |
| `coloring_page` | 339 | Coloring Books | Books, Movies & Music > ... > Coloring Books |
| `greeting_card_design` | 1280 | Just Because Cards | Paper & Party Supplies > Greeting Cards > Just Because Cards |
| `sticker_sheet_design` | 1326 | Stickers | Paper & Party Supplies > Stickers, Labels & Tags > Stickers |
| `pdf_planner_or_guide` | 354 | Calendars & Planners | Paper & Party Supplies > ... > Calendars & Planners |
| `pod_apparel_design` | 482 | T-shirts | Clothing > Gender-Neutral Adult Clothing > ... > T-shirts |

Notes:
- Etsy's taxonomy has **no distinct "phone wallpaper" leaf** — reused
  Digital Prints for both formats.
- `greeting_card_design`: picked "Just Because Cards" (1280) over the
  parent "Greeting Cards" (1261) — 1261 itself has 20 occasion-specific
  children (Anniversary/Birthday/Wedding/etc.) and is NOT a true leaf; a
  customizable, any-occasion card fits no single occasion child, so
  "Just Because" (Etsy's own catch-all for non-occasion-specific cards)
  is the correct specific leaf.
- `pdf_planner_or_guide`: "Calendars & Planners" (354) DOES have 4
  children, but all 4 are calendar sub-types (Advent/Desk/Pocket/Wall) —
  Etsy's tree has **no dedicated "Planners"-only leaf anywhere**, so 354
  itself is the best real match for a multi-page planner PDF.
- `pod_apparel_design`: picked the unisex-adult-tee leaf (482) as a
  **static default**. Etsy's tree has separate T-shirt leaves per
  demographic (449 men's / 559 women's / 11136 boys' / 11143 girls' / 482
  gender-neutral adult) and Printify blueprints vary by apparel type
  (hoodie/tee/tank/etc.), so a single static mapping is a simplification.
  Building blueprint-title-to-taxonomy matching is flagged as a real
  follow-up, not implemented here (materially larger feature, deferred
  per the ticket's own "check whether this needs..." framing).

**Step C — fix going forward:**
- `app/core/product_formats.py`: each `PRODUCT_FORMATS` entry now carries
  a real `taxonomy_id` from the table above (see the module docstring for
  the full sourcing/reasoning).
- `app/services/pipeline_orchestrator.py` (`_stage_create_listing`): sends
  the format-specific `taxonomy_id` at creation, then **independently
  re-fetches the listing** (new `EtsyClient.get_listing()`) and confirms
  the real `taxonomy_id` matches what was requested — same "generate →
  independently confirm via Etsy's own response" pattern as every other
  readback check this session. A mismatch (Etsy silently falling back to
  a different category, or any future wrong mapping) deletes the listing
  and blocks the task, exactly like every other readback failure.
- `EtsyClient`: added `get_listing()` (readback) and `update_listing()`
  (used for Step D below), both shop-scoping verified the same way as
  every other endpoint this session (`get_listing` is NOT shop-scoped,
  matching `delete_listing`; `update_listing`/PATCH IS shop-scoped,
  matching `publish_listing`).

**Step D — fixed existing listings.** Queried Etsy's own
`GET /shops/{shop_id}/listings` across every state (active/draft/expired/
inactive/sold_out) — the definitive source, not task-table inference —
and cross-referenced titles against known tasks:

| listing_id | title | in scope? | action |
|---|---|---|---|
| 4534427807 | Customizable Family Recipe Greeting Card (task `fb66a81a`) | yes — active, real product | **Fixed: taxonomy_id 1 → 1280, confirmed via independent readback** |
| 4534356981 | AR Home Decor Visualizer (task `7941465b`) | already `BLOCKED_NO_PRODUCT`, draft | **Not fixed — see below** |
| 4534362096 | Eco-Friendly Digital Downloads (task `97f0e7a0`) | already `BLOCKED_NO_PRODUCT`, draft | **Not fixed — see below** |
| 4533519279 | "test" (physical, taxonomy 355 "Movies") | not created by this system — no matching task | out of scope, untouched |
| 4533604970 | Handmade Ceramic Cat Paw Planter (physical, taxonomy 11254) | not created by this system — no matching task | out of scope, untouched |
| 1899837005, 1900562269, 1899934969 | old birthday-card listings, taxonomy 1264 (already a real leaf) | pre-dates this system (low listing IDs, no matching task) | out of scope, untouched |

**Only one listing (`4534427807`) was actually updated** — dry-run
printed the real current/new taxonomy, then applied, then independently
re-fetched to confirm `taxonomy_id: 1280` took effect. Task `fb66a81a`'s
`output_data` annotated with what was done.

**New finding, out of this ticket's scope but worth flagging:** the two
already-blocked draft listings (`4534356981`, `4534362096`) are still
sitting on Etsy as drafts — their `_cleanup_unbacked_listing()` delete
call apparently failed silently at the time (caught by its own
try/except, alert-only) rather than actually removing them. Left
untouched here since fixing taxonomy on an already-invalid, blocked
product doesn't accomplish anything — but Maj may want these two drafts
actually deleted, which is a different bug (why did the cleanup delete
fail?) than this ticket's taxonomy investigation.

---

### Tests

`scripts/test_step93_taxonomy_readback.py` (new, 3/3 pass):
1. Every `PRODUCT_FORMATS` entry has a specific `taxonomy_id` (not the old
   missing/default-1 shape).
2. Orchestrator sends the correct format-specific `taxonomy_id` in the
   `create_draft_listing()` payload (verified via a recording test
   double).
3. Readback shows a different `taxonomy_id` than requested (Etsy silently
   assigned something else) → listing deleted, task blocked — same
   pattern as every other readback failure this session.

All 18 test suites (steps 68–93) re-run and pass. Several existing test
doubles needed a `get_listing()` method added (mirroring the earlier
`get_listing_files`/`get_listing_images` additions) since every task that
reaches `create_listing` now triggers this new readback call.

---

### Files touched
- `app/core/product_formats.py` — real per-format `taxonomy_id`.
- `app/services/pipeline_orchestrator.py` — sends + readback-verifies `taxonomy_id`.
- `app/services/etsy_client.py` — new `get_listing()`, `update_listing()`.
- `scripts/test_step89/90/91/92_*.py` — test doubles updated with `get_listing()`.
- `scripts/test_step93_taxonomy_readback.py` — new.
- `scripts/fix_taxonomy_4534427807.py` — one-off production fix (already run).
