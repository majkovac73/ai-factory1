# STEP 103 — Growth & Revenue Audit (2026-07-10)

Second full-project audit, done after every step-102 fix landed (verified in
code on branch `audit-step102-fixes`, main c3f2a35 + P1-1 in 55a68f0). The
step-102 audit asked "what BREAKS the money flow" — those plumbing bugs are
fixed. This audit asks the next question: **what makes the shop actually earn,
and earn more** — business strategy, product picking, discoverability,
conversion, compliance risk, and the remaining technical gaps.

Ordered most → least beneficial. Each finding says WHAT, WHERE, WHY it matters
for revenue, and HOW to fix it — written so each can be worked as its own step
with Claude Code.

Current production reality assumed throughout: AUTONOMY_ENABLED=true,
AUTO_PUBLISH_LISTINGS=true, ~10 tasks/day cap, $5/day spend cap, Etsy shop
live via Railway, Printify shop 28166438, Pinterest NOT connected, Tumblr
connected (blog `productsforall`), marketing-refresh worker default OFF.

---

## TIER A — Growth engines. These decide whether the factory converges on money or wanders randomly.

### A-1. The learning loop is OPEN: performance data exists but steers NOTHING
- **Where:** `app/services/best_products_service.py` (insights computed, no
  consumer), `app/services/performance_service.py` (scores computed on demand
  only), `app/agents/trend_research_agent.py:76-119` (`run()` never receives
  any history), `app/workers/autonomy_worker.py:86-152` (creates tasks with
  zero knowledge of what sold).
- **What:** The factory now records real revenue per task (P0-8) and can rank
  best products — but the concept generator never sees any of it. Every cycle
  starts from scratch: same 8 Google Trends seeds → LLM insight → one product.
  Nothing exploits winners (make more like what sold), nothing avoids losers
  (formats/themes that never got a view). This is THE difference between a
  slot machine and a compounding business.
- **Fix (three sub-steps, each independently valuable):**
  1. **Feed insights into concept generation:** in `TrendResearchAgent.run()`,
     call `BestProductsService().get_best_product_insights()` +
     `RevenueService().get_revenue_by_task()`; inject into
     `_build_concept_prompt` a short block: "Products that actually earned or
     scored best so far: {titles, formats, keywords}. Products that earned
     nothing: {formats/themes with N listings and $0}. Bias toward proven
     themes/formats; propose a NEW product, not a copy."
  2. **Exploit winners with variants:** when a `sale_recorded` event lands for
     a task (hook in `EtsyReceiptWorker._record_revenue`), enqueue ONE
     follow-up concept task seeded from the sold product's title/keywords with
     metadata `{"source": "winner_variant", "parent_task_id": ...}` — capped
     (e.g. max 2 variant tasks/day, reuse AutonomyService caps). A product
     with a real stranger's money behind it is worth 10 fresh guesses.
  3. **Format budget:** the concept LLM currently free-picks among 7 formats.
     Track listings-per-format vs revenue-per-format and state a target mix
     in the prompt (e.g. "we already have 40 unsold phone wallpapers — do not
     propose another unless the insight is exceptional").
- **Verify:** unit test that the concept prompt contains the insights block;
  create a fake `sale_recorded` event → exactly one variant task appears,
  respecting caps.

### A-2. Products are picked from Google search data, never validated against Etsy BUYER data
- **Where:** `app/services/trend_data_service.py` (Google Trends only),
  `app/agents/market_intelligence/research.py` (reasoning grounded only in
  that), nothing anywhere calls Etsy's public search.
- **What:** Google Trends measures what people *google*, not what they *buy on
  Etsy*. Etsy's own API exposes real marketplace data for free with just the
  API key you already have: `GET /v3/application/listings/active?keywords=...`
  returns the live listing count (competition), and the top results carry
  real prices and titles (what winning sellers actually charge and how they
  write titles). A concept that looks hot on Google can be a graveyard on
  Etsy (100k competing listings) or priced far below your band.
- **Fix:** new `EtsyMarketService` with one method
  `validate_concept(keywords) -> {competition_count, price_p25/p50/p75,
  top_titles[:10]}` calling `findAllListingsActive` (api-key header only, no
  OAuth). Wire it in two places:
  1. **Concept gate:** in `TrendResearchAgent._propose_product`, after schema
     validation and before/alongside the viability critic — reject or
     retry-with-feedback when competition is extreme AND the concept has no
     differentiator, and attach `market: {...}` to the returned product dict.
  2. **Ground pricing + SEO:** pass `price_p50` into the listing stage (clamp
     band midpoint → market median when available) and pass `top_titles` into
     the executor prompt as "real titles currently winning for this niche —
     mine their long-tail keyword patterns, do not copy them".
- **Why first-class:** this is real buyer-side signal for $0 extra spend, and
  it upgrades pricing, SEO, and product selection simultaneously.
- **Verify:** log validate_concept output per cycle; unit test with a stubbed
  Etsy response; spot-check one concept's market numbers against etsy.com
  search by hand.

### A-3. Nothing stops the factory from building the same product twice (or fifty times)
- **Where:** `app/agents/trend_research_agent.py` (no memory of past
  concepts), `app/workers/autonomy_worker.py` (no dedup before
  `create_task`).
- **What:** Same 8 seed keywords + same prompt every hour → the LLM will
  converge on the same "boho wall art / budget planner / cottagecore
  coloring page" attractor concepts. Near-duplicate listings cannibalize each
  other in Etsy search, make the shop look like spam (Etsy's algorithm and
  human curators both notice), and waste the full ~$0.40-0.80 build cost each
  time.
- **Fix:**
  1. In `AutonomyWorker._run_cycle` (or the agent), load the last ~50 product
     tasks' `metadata_.product_name` + `type`; inject into
     `_build_concept_prompt`: "Products already in the shop — your proposal
     must be clearly different from ALL of these: {list}".
  2. Hard guard after generation: normalized-token-overlap (or
     `difflib.SequenceMatcher` ratio > 0.75 against any recent title of the
     same format) → treat like a validation failure (retry with feedback).
     No new dependency needed.
- **Verify:** unit test: candidate title nearly identical to an existing task
  → rejected with dedup feedback; live: two days of autonomy produce no
  near-duplicate titles.

### A-4. Etsy SEO is running at half power: ~3-5 tags used of 13, thin descriptions, no grounding in real queries
- **Where:** `app/core/schemas/seo_schema.py:10` (keywords `min_length=3` —
  the LLM routinely stops at 3-5), `app/agents/etsy/listing_generator.py:26-41`
  (tags = keywords passthrough), `app/core/agents/executor.py:37-44`
  (no tag-count/format guidance, description only "120+ chars, natural"),
  trend `rising_queries` never reach the SEO stage (autonomy prompt at
  `autonomy_worker.py:128-133` carries only name/description/audience).
- **What:** Tags are Etsy's primary matching surface — every unused tag slot
  is a search you can never appear in. Multi-word tags ("boho wall decor")
  match phrase searches; 13 of them ≈ 13 lottery tickets vs your current 3-5.
  Descriptions: Etsy uses the beginning for search/Google snippets, and
  buyers decide on "what exactly do I get?" — a free-form 120-char blurb
  answers neither. And the ONE piece of real search data the system fetches
  (Google rising queries) is thrown away before SEO happens.
- **Fix:**
  1. Schema: `keywords: min_length=13, max_length=13`; validator: each 2-3
     words, ≤20 chars (Etsy tag limit), deduplicated.
  2. Executor prompt: ask for exactly 13 buyer-search-phrase tags (mix of
     head + long-tail), and a STRUCTURED description: hook sentence
     (≤160 chars, keyword-rich), "WHAT YOU GET" bullet block, "HOW IT WORKS"
     (instant download / printing notes), sizes/specs, usage terms (personal
     use only). Per-format blocks templated in code, LLM fills the creative
     parts — deterministic sections can't be hallucinated away.
  3. Thread trend queries through: autonomy worker puts
     `rising_queries`/`market.top_titles` (A-2) into task
     `metadata_["seo_context"]`; `TaskProcessor._execute` appends it to the
     executor context.
  4. Materials: send `["digital download","printable art"]`-style values for
     digital instead of the current always-empty list (`pipeline_orchestrator.py:1043`).
  5. One-off backfill script: `scripts/backfill_seo_tags.py` — for existing
     active listings with <13 tags, regenerate tags only and PATCH via
     `EtsyClient.update_listing` (cheap text call per listing; every already-
     published product benefits immediately).
- **Verify:** new listing carries exactly 13 tags in Etsy readback; backfill
  dry-run prints before/after tags for approval first.

### A-5. Deliverables don't match what buyers of each format actually expect (biggest refund/review risk after step 102)
- **Where:** `app/core/product_formats.py` (`single_print` delivers ONE 1:1
  square PNG; `phone_wallpaper` one 9:16 PNG), `pipeline_orchestrator.py`
  step 2.6 (one delivery file uploaded).
- **What:** Wall-art buyers on Etsy expect a MULTI-RATIO bundle (2:3, 3:4,
  4:5, 11×14, A-series — top sellers advertise "5 sizes included"); a single
  square file that fits no standard frame gets returns and 3-star reviews.
  Phone-wallpaper buyers expect at least a couple of device sizes. Etsy
  allows **5 digital files per listing** — the pipeline uploads 1.
- **Fix (zero image-generation cost — pure PIL):**
  1. New `DeliveryBundleService`: from the content-verified master design,
     produce ratio variants by smart center-crop/pad (2:3, 3:4, 4:5, ISO A,
     original) for `single_print`; 2-3 resolutions for `phone_wallpaper`;
     letter-size PDF wrap for `coloring_page` (buyers print these — a
     ready-to-print PDF at 8.5×11 with margins reviews better than a raw PNG).
  2. Generate the master at 4K portrait (3:4 or 2:3) instead of 1:1 for
     `single_print` so crops have real estate — change `delivery_aspect` and
     let the existing 4K-for-non-square logic handle the pixel floor.
  3. `EtsyImageService.attach_images_and_publish`: accept a LIST of digital
     files (up to 5), upload each, readback-verify count.
  4. Update description template (A-4): "Includes N sizes: ...".
- **Verify:** buy-side check of a test listing shows 5 files; PIL unit test
  asserts each ratio's dimensions; reviews/returns tracked after.

### A-6. PDF planners are built the most expensive, lowest-quality way possible — and capped at 6 pages in a market selling 20-150 pages
- **Where:** `app/services/pdf_generation_service.py` (every page = one $0.04
  Seedream call, garbled-text risk per page, per-page vision QA on top),
  `config/settings.py:135` (`MAX_PDF_PAGES=6`).
- **What:** Planner pages are STRUCTURED LAYOUTS (grids, lines, checkboxes,
  headings) — the one thing a deterministic renderer does perfectly and an
  image model does worst (misspelled headings, wonky grids caught by QA →
  retries → cost). Six pages at $5-12 against competitors' 30-page planners
  is a losing product. Current true cost/planner: ~$0.30-0.50 + QA; a
  code-rendered planner is ~$0.001 and can be 30 pages.
- **Fix:** hybrid rendering:
  1. Keep ONE generated image: the decorative cover (that's what Seedream is
     good at).
  2. Interior pages: LLM produces a page SPEC as JSON (`{heading, layout:
     "weekly_grid"|"checklist"|"lined"|"tracker_table"|..., labels[]}`), and a
     new `PlannerPageRenderer` draws it with Pillow (`ImageDraw` +
     `ImageFont.truetype` — ship 1-2 OFL fonts in `assets/fonts/`) or
     reportlab. Text is ALWAYS legible; per-page vision QA becomes
     unnecessary for rendered pages (keep a cheap structural check).
  3. Raise `MAX_PDF_PAGES` to 30 for rendered interiors; keep the image-gen
     path (and its 6-page cap) only for art-heavy formats if wanted.
  4. Update the price band upward once page counts are competitive
     (planners with 25+ pages sell $8-15).
- **Verify:** generate a 20-page planner locally; pypdf readback; open it —
  every heading crisp; ledger shows ~1 image call instead of 6+.

### A-7. Nothing targets seasonality — the factory always builds for TODAY
- **Where:** `trend_data_service.py` (`timeframe="today 3-m"` — trailing
  data), `_RESEARCH_TOPIC` (no calendar awareness).
- **What:** Etsy buyers shop occasions 4-10 weeks ahead (Christmas printables
  peak in October; Valentine's cards in early January; back-to-school
  planners in July). Trailing Google data catches a wave as it crests —
  after Etsy's ranking has already consolidated winners with reviews. The
  automated shop is structurally late to every occasion.
- **Fix:**
  1. Small static calendar in code: `{month: [upcoming occasions 4-10 weeks
     out]}` (Christmas, Valentine's, Mother's/Father's Day, Halloween,
     back-to-school, New Year/planning season, graduation, wedding season).
  2. Inject into research + concept prompts: "It is {date}. Buyers are
     currently shopping for: {occasions}. Weight concepts toward these when
     the trend data supports it."
  3. Optionally add 1-2 seasonal seed keywords dynamically (e.g.
     "christmas printable" from Sep-Nov) to the pytrends pull.
- **Verify:** July run proposes back-to-school/Halloween-adjacent concepts,
  not Christmas-in-July or generic-only.

### A-8. Listings go live with 2-3 photos; Etsy gives you 10, and photos are the conversion surface
- **Where:** `pipeline_orchestrator.py:_build_listing_mockups` (2 composites),
  `_stage_pod_listing_images` (≤3 Printify mockups).
- **What:** Etsy's own seller guidance and every conversion study agree:
  5-8 photos strongly outperform 2. For digital products all extra photos are
  FREE (PIL composites of the already-verified design — scene cache from
  P3-6 already exists).
- **Fix:** extend `MockupService` with more deterministic roles: close-up
  detail crop, second room scene (reuse/expand the cached scene pool),
  "sizes included" infographic card (A-5 bundle diagram, drawn with
  Pillow), "how instant download works" card (static template shared by all
  listings), for coloring pages a "printed + partially colored" flat-lay
  scene. Target 6 photos/listing. For POD, fetch up to 6 Printify mockup
  renders instead of 3 (`[:3]` → `[:6]`, `printify_client.get_product` already
  returns them all).
- **Verify:** new listing shows 6 images in Etsy readback; visual spot-check.

### A-9. Pinterest — the #1 organic channel for exactly these products — is still dark, while the only live channel (Tumblr) has ~zero reach
- **Where:** `settings.PINTEREST_APP_ID=None` (not configured; channel code +
  oauth + image service all built and tested), marketing = Tumblr only.
- **What:** Printables/planners/wall art are Pinterest's core commercial
  categories; established printable sellers get the majority of external
  traffic from it. The entire integration (pin image generation, channel,
  OAuth) is already written — it's inert for lack of an app registration.
  Meanwhile every product burns a Tumblr post to a blog with no followers.
- **Fix (mostly a Maj-manual step, then config):**
  1. Register a Pinterest developer app (business account, request standard
     API access), set PINTEREST_APP_ID/SECRET/REDIRECT_URI/BOARD_ID in
     Railway, run `/pinterest/oauth/login` once.
  2. Create 3-5 themed boards (wall art / planners / coloring pages) and,
     when posting, pick the board by product format (small mapping in
     `PinterestChannel`; today it posts to the single configured board).
  3. Turn on MARKETING_REFRESH_ENABLED once Pinterest is live — re-pinning
     the existing catalog weekly is exactly what that worker was built for
     and it's currently OFF, so even Tumblr re-promotion isn't happening.
  4. Tumblr (keep, it's free): add tags to posts if not already — discovery
     on Tumblr is 100% tag search.
- **Verify:** pin appears on the right board with the listing URL; Etsy
  stats (A-10) show pinterest referral visits over the following weeks.

### A-10. The shop is blind to views/favorites — the earliest sales signal Etsy offers
- **Where:** nowhere in `app/` is `views` or `num_favorers` read (grep
  confirms); `PerformanceService` weights revenue 50 / reliability 30 /
  marketing-post-success 20 — i.e. before the first sale, "performance" is
  just "did the pipeline not crash".
- **What:** Sales are rare events early; views/favorites arrive 100x sooner
  and tell you which products/keywords Etsy is showing to real buyers.
  `getListingsByShop` returns `views` + `num_favorers` for 100 listings per
  call — the whole shop is 1-5 calls/day.
- **Fix:**
  1. Daily poll (fold into `EtsyReceiptWorker` as a once-per-day tick, or a
     tiny new worker): fetch all active listings, record an analytics event
     `listing_stats {listing_id, task_id, views, favorites}` (resolve
     task_id via the existing `ImageAsset.listing_id` / PODProduct mapping).
  2. `PerformanceService`: replace the 20-point marketing-post score with an
     engagement score (views + 10×favorites, capped), keep revenue at 50.
  3. Surface "views yesterday / favorites total / conversion" on the
     dashboard per product; this data also powers A-1's insight block and
     C-5's pruning.
- **Verify:** after two polls, `listing_stats` events exist with increasing
  timestamps; scores change for viewed products; dashboard shows the numbers.

---

## TIER B — Product-portfolio decisions (what to build, what to stop building)

### B-1. POD sells ONE size in ONE color — decide: real variations, or pause POD
- **Where:** `pod_fulfillment_service.py:_pick_single_variant` (deliberate
  single variant, honest per step-102 P0-5 stage-2), listing has no Etsy
  variations.
- **What:** A t-shirt listing where the buyer cannot pick a size converts
  near zero — the current setup is honest but commercially dead weight. Each
  POD task still costs full pipeline money. Either finish the job or stop
  paying for it.
- **Fix (pick one):**
  - **(a) Real variations (the money path):** enable ~8 variants
    (S-XL × black/white) on the Printify product; mirror them as Etsy
    variations via `updateListingInventory` (products[] with Size/Color
    property_values, price per variant from P0-4 math using max variant
    cost); in `EtsyReceiptWorker._process_receipt` map the transaction's
    `variations` to the matching Printify variant_id (transactions carry the
    chosen property values) instead of `variant_ids[0]`.
  - **(b) Pause POD:** remove `pod_apparel_design` from the concept prompt's
    allowed formats (one line in `trend_research_agent.py`) until (a) is
    scheduled. Zero code risk, immediate spend saving.
  - Recommendation: (b) now, (a) as its own step soon — POD margins per unit
    ($6 target profit) are the best in the portfolio once size choice works.
- **Verify (a):** fake receipt with "Size: M / Black" → Printify order for the
  M/Black variant id; listing shows a size picker on Etsy.

### B-2. Greeting cards are sold as "cards" but delivered as flat art squares
- **Where:** `product_formats.py` (`greeting_card_design` → single 3:4 image).
- **What:** A buyer of a printable card expects a print-ready HALF-FOLD PDF
  (front art / blank inside / small back) — printing a flat PNG doesn't fold
  into a card. Mismatch = refunds and 1-stars on an otherwise fine design.
- **Fix:** small deterministic assembler (Pillow, same skills as A-6): place
  the generated art on a letter/A4 half-fold layout (art bottom-right when
  folded, inside blank or one LLM greeting line, back with a tiny logo),
  deliver PDF + the original PNG. Update description template accordingly.
- **Verify:** print the PDF, fold it — art lands on the front, upright.

### B-3. Add the missing single-image formats with proven Etsy demand
- **Where:** `app/core/product_formats.py` (7 formats).
- **What:** Two formats fit the existing single-image pipeline exactly and
  have strong evergreen demand:
  1. `seamless_pattern` (digital paper): one square image, buyers are
     crafters/print shops; validation can even CHECK seamlessness in code
     (compare opposite edges within tolerance). Taxonomy: Craft Supplies >
     ... (fetch real leaf via existing get_taxonomy script). Price band
     ~$3-6.
  2. `wall_art_set_3`: three coordinated prints sold as one listing —
     top-selling digital wall-art category ("gallery wall set"). This is a
     DELIBERATE, spec'd multi-asset product (3 delivery files of the same
     concept), not the vague "bundle" the multi-item ban targets — implement
     as a new `delivery: "image_set"` path that runs the single-image stage
     3× with one shared visual brief + per-piece brief, uploads 3 files
     (A-5's multi-file support), mockups show all three framed together.
     Update `_MULTI_ITEM_MARKERS` validator to allow exactly this format.
- **Verify:** one live listing per new format passes all existing gates;
  seamlessness unit test with an intentionally non-tiling image fails.

### B-4. Quote/typography prints will keep tripping content-QA — render text deterministically
- **Where:** `pod_pipeline_service.py`/`pod_design_agent.py` (all delivery art
  is one Seedream call; garbled text is the #1 content-QA failure class per
  step 96's rationale).
- **What:** For text-centric concepts (quote prints, affirmation cards) the
  image model misspells; QA catches it, retries burn $0.04 + vision calls,
  and stubborn cases block the task after money was spent.
- **Fix:** when the concept is text-led (flag from the concept LLM:
  `"text_led": true`), generate the BACKGROUND/decoration with Seedream
  (prompt: "no text") and set the typography with Pillow using the bundled
  fonts (A-6) — exact spelling guaranteed, QA passes deterministically.
- **Verify:** text-led task produces pixel-crisp correctly-spelled type; QA
  retry rate for these tasks drops to ~0 in logs.

### B-5. Concept + SEO quality run on the cheapest model — the two highest-leverage LLM calls in the system
- **Where:** `config/settings.py:21` (`DEFAULT_MODEL="openai/gpt-4o-mini"`
  for ALL text agents via BaseAgent).
- **What:** At 10 products/day the concept and SEO calls total well under
  $0.05/day on mini. The entire business outcome hinges on exactly these
  outputs (what to build, how it's found). A frontier model
  (claude-sonnet-5, gpt-4o, etc. via OpenRouter) costs cents more per day
  and measurably improves concept specificity and tag quality.
- **Fix:** add `CONCEPT_MODEL` and `SEO_MODEL` settings (default =
  DEFAULT_MODEL); pass into `TrendResearchAgent`/`ProductViabilityCriticAgent`
  and `ExecutorAgent` respectively. Set them to a stronger model in Railway;
  compare a week of viability-critic scores and listing quality by eye.
- **Verify:** logs show the configured models per agent; spend ledger delta
  stays under ~$0.10/day.

### B-6. Listing videos — Etsy favors them and the API supports upload
- **Where:** `etsy_image_service.py` (images + files only; no
  `uploadListingVideo`).
- **What:** Etsy search rewards listings with video and buyers convert
  better; for digital art a 5-10s slow zoom/pan over the mockup is enough.
  Feasible without ffmpeg binaries: `imageio`/`imageio-ffmpeg` wheel writing
  an MP4 from ~90 PIL-generated Ken Burns frames of the hero mockup.
- **Fix:** `VideoMockupService.build(design_path) -> mp4 bytes` +
  `EtsyImageService.upload_listing_video`; best-effort stage (video failure
  never blocks a listing). Cap file size per Etsy limit (100MB — ours will
  be ~2MB).
- **Verify:** listing shows the video on Etsy; pipeline unaffected when the
  stage is disabled/fails.

### B-7. Shop presentation & sections — one-time manual + small API step
- **What:** Buyers who click through see an anonymous default shop: no
  sections, and (verify) possibly thin about/policies/banner. Trust =
  conversion, especially with zero reviews.
- **Fix:**
  1. API: create shop sections per format ("Wall Art", "Planners", "Coloring
     Pages", "Wallpapers", "Cards & Stickers") via
     `POST /shops/{shop_id}/sections` once, store the mapping in
     `product_formats.py`, send `shop_section_id` on listing creation.
  2. Manual (Maj, 1 hour): shop banner/icon (generate with the existing image
     pipeline!), about section, FAQ, policies, announcement. Write the copy
     with Claude, paste in Shop Manager.
- **Verify:** new listings land in the right section; shop page looks
  intentional.

### B-8. Manual growth playbook (things code can't do — recurring Maj actions)
- **What/Fix:** keep a short recurring checklist (this file is its spec):
  - Etsy **sales events** (Shop Manager → Marketing): run a 15-25% sale every
    2-3 weeks — Etsy pushes sale listings and emails favoriters (favorites
    from A-10 make this compound). API can't do this; 10 minutes by hand.
  - **Etsy Ads** at $1-3/day ONLY on products with revenue or strong
    views→favorite ratios (data from A-10) once ~5+ sales exist shop-wide.
  - Reply to every review; pin best reviews.
  - Monthly: skim `GET /dashboard` best-products vs the Etsy stats screen;
    adjust `TREND_SEED_KEYWORDS` env with what's converting.

---

## TIER C — Protect the business (one bad event here outweighs months of growth)

### C-1. No trademark/IP screening anywhere — the single biggest existential risk to the shop
- **Where:** `trend_research_agent.py` (concepts come straight from rising
  Google queries — which are FULL of brand/celebrity/character terms),
  executor tags, POD designs. No filter at any stage.
- **What:** Google's rising queries surface "taylor swift wallpaper",
  "stanley cup accessories", "bluey coloring pages" precisely BECAUSE they
  trend. One AI-generated "Bluey coloring page" listing = takedown; repeat
  offenses = permanent shop suspension (Etsy is aggressive; suspension kills
  the whole project including every legitimate listing). This risk grows
  every single autonomous day.
- **Fix (layered, cheap):**
  1. Static blocklist (module constant + env-extendable): obvious
     brand/character/celebrity terms; checked against concept name,
     description, tags, and the trend queries fed into prompts (drop poisoned
     queries before the research prompt sees them).
  2. LLM screen as part of the viability critic call (zero extra calls): add
     to its rubric "If the concept references or clearly derives from a
     trademarked brand, franchise, character, celebrity, sports team, or
     another artist's signature style, score it 1 and say why." — fail
     closed.
  3. Same check on the final 13 tags before listing creation (tags are what
     rights-holders' bots scan).
- **Verify:** unit tests: "bluey coloring page" concept and a "swiftie"
  tag both rejected; clean concepts unaffected.

### C-2. Etsy policy compliance gaps: production partner not declared for POD; AI-generated nature not disclosed
- **Where:** `etsy_client.py:create_draft_listing` (no
  `production_partner_ids`; `who_made: "i_did"` on everything), description
  templates (no creation-process disclosure).
- **What:** Etsy's Creativity Standards (2024+) require sellers to (a)
  declare production partners — Printify is one; POD listings without a
  declared partner are policy violations rights-holders and Etsy sweeps
  catch, and (b) accurately describe how items are made — AI-assisted design
  should be disclosed in the listing/shop. These are shop-suspension vectors,
  same blast radius as C-1.
- **Fix:**
  1. Maj manual (once): add Printify as a production partner in Shop Manager
     (Settings → Production partners); then
     `GET /v3/application/shops/{shop_id}/production-partners` for the id;
     set `ETSY_PRODUCTION_PARTNER_ID` env; send
     `production_partner_ids: [id]` on POD listing creation.
  2. Add one honest line to the description template (A-4): "Original design
     created by [shop] using AI-assisted tools." — and mirror it in the shop
     About section. Review Etsy's current policy text while implementing
     (it evolves).
- **Verify:** POD listing readback shows the production partner; description
  carries the disclosure.

### C-3. Zero backups: one Railway volume failure loses the tokens, the catalog, the ledger, and all revenue history
- **Where:** SQLite at `/data/app.db` + `data/images` + autonomy/receipt
  state JSONs live on a single Railway volume; no backup code anywhere in
  the repo.
- **What:** The DB holds OAuth tokens (re-auth is manual), PODProduct↔listing
  mappings (fulfillment breaks without them — paying customers affected),
  the image catalog, and every analytics/revenue event the learning loop
  (A-1) depends on. Volume corruption or accidental deletion is a
  business-history wipe.
- **Fix:** nightly job (fold into a worker's daily tick): `sqlite3` online
  backup (`sqlite3.Connection.backup`) + the two state JSONs into a
  timestamped zip; upload off-box — simplest reliable free option:
  Cloudflare R2/Backblaze B2 via boto3-compatible API (env-configured);
  fallback if no bucket configured: keep last 7 zips on the volume (better
  than nothing) and alert weekly that offsite backup is unconfigured. Add
  `POST /admin/backup` for manual runs. Images can be excluded initially
  (regenerable at cost); the DB cannot.
- **Verify:** restore drill: download a backup zip, open the DB copy, count
  rows; document the restore steps in the changelog.

### C-4. Security/ops checklist — items code already supports but prod state is unverified
- **What (each is a 5-minute Railway/manual action; verify, don't assume):**
  1. `FACTORY_API_KEY` actually SET in Railway (enforcement is off when
     unset — `app/api/auth.py:44-46`); curl a POST without the header → must
     be 401.
  2. `ENV=production`, `DEBUG=False` set (defaults are dev/True —
     `config/settings.py:11-12`).
  3. **Rotate Etsy + Tumblr tokens** — the old app.db WAS committed to git
     history (step-102 P3-1); if that repo ever touched a remote, treat the
     tokens as leaked. Re-run both OAuth flows, confirm old refresh tokens
     invalidated.
  4. Run `scripts/audit_existing_listings.py` against the live shop once
     (pre-102 listings may carry old defects: taxonomy, when_made, missing
     files).
  5. `TREND_SEED_KEYWORDS` env, if set, must be JSON
     (`'["printable wall art", ...]'`) — a comma string fails pydantic
     parsing at boot.
- **Verify:** each has an explicit check listed above.

### C-5. Listing fees compound silently: $0.20 × every listing × renewals, with no pruning and no ledger entry
- **Where:** `pipeline_orchestrator.py:_stage_create_listing` (fee not
  recorded in AutonomyService ledger — P0-13 covered images/vision only),
  no code deactivates anything ever.
- **What:** At 10 listings/day: $60/month creation fees + auto-renewals
  ($0.20/listing every 4 months) on a growing pile of zero-view listings.
  Within a year that's a few hundred dollars/year of pure fee burn on dead
  inventory — plus a shop full of unsold listings drags perceived quality.
- **Fix:**
  1. One line: `AutonomyService().record_spend(0.20, "etsy listing fee")` on
     successful listing creation (it's real, same-day money).
  2. Pruning (needs A-10 stats): monthly job flags active listings older
     than ~100 days with 0 sales AND views below a threshold →
     `update_listing(state="inactive")` before their renewal date; report
     the list to Discord first (dry-run mode) until trusted.
- **Verify:** ledger includes listing fees; first prune run in dry-run mode
  lists candidates without deactivating.

---

## TIER D — Technical/cost improvements (real but smaller money impact)

### D-1. Trend data is re-fetched from Google every hour — ban risk for zero benefit
- **Where:** `trend_data_service.py` (fresh pytrends session + 8 keywords ×
  2 requests per autonomy cycle, 24 cycles/day from one Railway IP).
- **What:** Google Trends aggressively 429s scrapers; a ban stops ALL
  autonomous cycles (fail-loud design = no tasks at all). Trends don't
  change hourly — this is pure wasted risk.
- **Fix:** cache the fetched signal dict to `data/trend_cache.json` with a
  12-24h TTL (setting `TREND_CACHE_HOURS=12`); serve from cache within TTL.
  Also pass `geo="US"` in `build_payload` — Etsy buyers are predominantly
  US, worldwide signal dilutes.
- **Verify:** two cycles within the TTL make zero pytrends HTTP calls
  (log line "trend cache hit").

### D-2. Two wasted LLM calls per product task (planner + listing-metadata)
- **Where:** `task_processor.py:_plan` (planner generates 3 steps; P2-2 then
  collapses to 1 — the call itself is now pointless for product formats),
  `listing_generator.py:generate_listing` (LLM invents
  price/category/quantity/shipping — price is clamped/overridden, category
  is replaced by the format's taxonomy_id, quantity is forced to 999,
  shipping comes from the profile service: ~nothing survives).
- **Fix:** in `_plan`, for `task.type in PRODUCT_FORMATS` skip the LLM and
  save a static one-step plan. In `_stage_create_listing`, build the listing
  dict directly from `output_data` + `clamp_price(band midpoint)` (or A-2's
  market median) and drop the ListingGeneratorAgent call for product formats
  (keep `_derive_tags`). Two fewer calls and two fewer JSON-parse failure
  modes per task.
- **Verify:** task logs show no planner/listing-metadata generations;
  listings unchanged in shape (existing step-89 tests still pass).

### D-3. Analytics scans degrade as events grow (already O(10k) per receipt transaction)
- **Where:** `revenue_service.py:has_sale_for_transaction` (loads 10k events
  per check), `performance_service.py:_marketing_score` (loads 2×1000 events
  per task → `score_all_tasks` over N tasks is O(N×2000)),
  `get_total_revenue`/`get_revenue_by_task` (limit 10000 hard cap — silently
  wrong once exceeded).
- **Fix:** add indexed columns/queries: store `transaction_id` as a proper
  column on AnalyticsEvent (or a small `sales` table) with a unique index;
  filter marketing events by task_id in SQL (JSON1 `payload->>'task_id'` or a
  real column). Do it before A-10 multiplies event volume (daily stats
  events per listing).
- **Verify:** EXPLAIN shows index use; scoring 500 tasks stays sub-second.

### D-4. Etsy listing images: no alt text (SEO + accessibility freebie)
- **Where:** `etsy_image_service.py` upload (v3 `uploadListingImage`
  supports `alt_text`; only the Tumblr channel sets alt text today).
- **Fix:** pass `alt_text=f"{product_name} — {role}"[:250]` per image upload.
- **Verify:** readback listing images carry alt_text.

### D-5. Move ad-hoc test scripts to pytest + a GitHub Actions gate
- **Where:** 80+ `scripts/test_*.py`, empty `tests/`, no CI (step-102 P3-7
  fixed the broken ones but didn't migrate).
- **What:** Every deploy of an unattended money system ships unverified; the
  suites exist but only run when someone remembers.
- **Fix:** move the deterministic suites into `tests/` as pytest (they're
  already mostly assert-based; add a `conftest.py` with the sys.path/tmp-DB
  bootstrap), add `.github/workflows/ci.yml` running `pytest` +
  `python -m compileall app config` on push. Keep Railway deploys manual
  from green main.
- **Verify:** CI red on an intentionally broken assert; green on main.

### D-6. Small correctness/hygiene notes (log everything, even small)
- `revenue_service.get_total_revenue` sums across currencies blindly — fine
  while USD-only; normalize or assert currency=="USD" when recording.
- Dashboard/analytics GETs are intentionally public (auth.py) — they expose
  spend/revenue numbers to anyone with the URL. Consider requiring the key
  for `/analytics` + `/dashboard` reads once the frontend sends it, or at
  least don't share the URL.
- `CORSMiddleware` uses `allow_origins=["*"]` with `allow_credentials=True`
  (`app/main.py:37-43`) — an invalid combo per the CORS spec (browsers
  reject it); harmless today, tighten to the Railway origin when the
  frontend needs credentials.
- POD margin math (`_pod_price_cents_from_cost`) ignores Etsy Offsite Ads
  (12-15% when attributed) — at low volume it's optional/rare; note it in
  the POD_ETSY_FEE_FRACTION comment or bump to 0.12 for safety.
- `MarketingRefreshWorker` posts oldest-first regardless of performance —
  once A-10 exists, weight refresh toward products with views/favorites.
- `_extract_pdf_pages` still writes temp PNGs outside the task folder
  (cleaned up now, but writing to `data/images/listing/{task}/` would keep
  useful assets — noted in step 102, still open).
- `frontend/index.html` dashboard has no revenue/spend-vs-earnings panel —
  add a simple P&L tile (ledger spend + listing fees vs recorded revenue)
  so the one number that matters is visible at a glance.
- Root-level `check_token.py`, `designsforall.txt`, `taxonomy_output.txt`
  still sit in repo root (flagged in 102 P3-8, unmoved).

---

## Suggested working order (each line = one Claude Code step)

1. **C-1 trademark gate + C-2 production partner/disclosure** — existential
   risk, small code. Do before more autonomous days accumulate.
2. **C-4 ops checklist** (30 min of Railway/manual verification, incl. token
   rotation) and **C-3 backups** — protect what exists.
3. **A-4 SEO overhaul (13 tags + structured descriptions + backfill)** —
   biggest immediate lift for every existing and future listing.
4. **A-2 Etsy market validation** + **A-3 dedup** — fix product PICKING.
5. **A-1 learning loop** + **A-10 views/favorites polling** (they're one
   theme: close the loop) — do A-10 first, it feeds A-1.
6. **A-5 multi-ratio delivery bundles** + **A-8 more photos** — conversion
   on the product page.
7. **B-1(b)** pause POD now; schedule **B-1(a)** variations as its own step.
8. **A-6 planner rendering overhaul** — biggest single product-quality jump.
9. **A-7 seasonality**, **A-9 Pinterest activation**, **B-7 shop sections**.
10. **B-2/B-3/B-4** new-format work, **B-5 model upgrade experiment**,
    **B-6 video**.
11. **C-5 fees/pruning**, then TIER D as maintenance (D-1 and D-2 are
    quick wins worth slotting early alongside step 4).

---

## Appendix A — how this audit was done / evidence

- Read end-to-end on branch `audit-step102-fixes` (clean tree, HEAD 55a68f0):
  `pipeline_orchestrator.py` (full), `trend_research_agent.py`,
  `trend_data_service.py`, `autonomy_worker.py`, `autonomy_service.py`,
  `performance_service.py`, `best_products_service.py`, `revenue_service.py`,
  `etsy_receipt_worker.py`, `pod_fulfillment_service.py`, `etsy_client.py`,
  `listing_generator.py`, `seo_schema.py`, `executor.py`, `planner.py`,
  `task_processor.py`, `marketing_refresh_service.py`, `main.py`, `auth.py`,
  `database.py`, `settings.py`, `product_formats.py`,
  `pdf_generation_service.py` (head), research/intelligence/critic agents.
- Verified all step-102 fixes present in code (margin math, retries, WAL,
  auth middleware, spend metering, resume, mockups-from-delivery, Printify
  mockups for POD) — none of those findings are repeated here.
- Grep evidence: no occurrence of `views`, `num_favorers`, `alt_text`
  (except Tumblr), `video`, `shop_section`, `production_partner` in `app/` —
  basis for A-10, D-4, B-6, B-7, C-2.
- No code executed against production; no listings/shop state touched.
- External facts relied on (verify current values when implementing): Etsy
  limits — 140-char title, 13 tags, 10 photos, 5 digital files ≤20MB,
  $0.20 listing fee + ~4-month auto-renew, 6.5% transaction fee; Etsy v3
  endpoints `findAllListingsActive`, `getListingsByShop` (views/favorites),
  `updateListingInventory` (variations), `uploadListingVideo`, shop sections,
  production partners; Pinterest/printables channel fit; Etsy Creativity
  Standards on AI disclosure + production partners. These are stable,
  well-documented Etsy platform facts as of early 2026 but each fix step
  should confirm against live docs/payloads (the codebase's own
  readback-verify pattern).
