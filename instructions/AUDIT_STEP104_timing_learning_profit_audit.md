# STEP 104 — Timing, Learning-Loop & Profit Audit (2026-07-11)

Third full-project audit, done after all of STEP 102 (money-flow plumbing) and
STEP 103 (growth engines) shipped. Trigger: Maj observed the factory generating
**seasonal products way too early or too late**. This audit traces that to
concrete, verified root causes in the seasonality/trend code, then covers
everything else found — learning-loop biases, pricing/P&L honesty, distribution
gaps, robustness, and still-open manual actions.

Ordered most → least important. Each finding: WHAT, WHERE, WHY it costs money,
HOW to fix, and how to VERIFY. Work them top-down as separate steps with Claude
Code.

Production reality assumed: AUTONOMY_ENABLED=true, AUTO_PUBLISH_LISTINGS=true,
MAX_TASKS_PER_DAY=10, MAX_DAILY_SPEND_USD=5, Railway + persistent volume,
Printify shop 28166438, Tumblr connected, **Pinterest NOT connected**,
**MARKETING_REFRESH_ENABLED=false**, POD paused (POD_APPAREL_ENABLED=false).

---

## TIER 1 — Seasonal product timing (the "too early / too late" bug, root-caused)

The seasonality feature (STEP 103 A-7, `app/core/seasonality.py`) is a good
skeleton but has four verified defects that together produce exactly the
symptom Maj saw. All four were reproduced by running the real code against real
2026 dates (results shown inline).

### 1-1. Movable holidays are hardcoded to WRONG dates for 2026+ (products mistimed by up to a week, worse every year)
- **Where:** `app/core/seasonality.py:12-24` (`_EVENTS`).
- **What:** Easter, Mother's Day, Father's Day and Thanksgiving move every
  year, but the table hardcodes one year's dates. Verified against 2026:
  - Easter: hardcoded Apr **9**, real 2026 date Apr **5** (4 days late; Easter
    ranges Mar 22–Apr 25, so future years can be off by weeks).
  - Mother's Day: hardcoded May 11 (2025's date), real 2026 = May 10.
  - Father's Day: hardcoded Jun **15**, real 2026 = Jun **21** (6 days off).
  - Thanksgiving: hardcoded Nov 27, real 2026 = Nov 26.
- **Why it matters:** every downstream window (`upcoming_occasions`,
  `seasonal_seed_keywords`, the concept-prompt block) inherits the error, so
  "buyers are shopping NOW" fires on the wrong days — a direct cause of
  slightly-too-early/too-late seasonal products, compounding each year.
- **Fix:** compute dates instead of hardcoding: Easter via the standard
  computus algorithm (pure-python, ~10 lines — no new dependency needed),
  Mother's Day = 2nd Sunday of May, Father's Day = 3rd Sunday of June,
  Thanksgiving = 4th Thursday of November, Graduation ≈ mid-May–mid-June (keep
  Jun 1 anchor). Keep fixed-date holidays as-is.
- **Verify:** unit test asserting the computed 2026/2027 dates (Easter 2026 =
  Apr 5, 2027 = Mar 28; Thanksgiving 2026 = Nov 26; Father's Day 2026 = Jun 21).

### 1-2. The 3-week minimum lead lists seasonal products TOO LATE to ever rank
- **Where:** `app/core/seasonality.py:27` (`min_weeks: int = 3` — the docstring
  even says "4-10-week window" while the code says 3), and the single global
  window for every event.
- **What (verified by running the code):**
  - On 2026-04-20 it tells the concept LLM buyers are shopping NOW for
    "Mother's Day (~3 weeks away, 21 days)". A brand-new listing created that
    day has zero reviews and typically needs 1–3 weeks for Etsy search to even
    settle its ranking; digital Mother's Day purchases peak ~2–5 weeks before
    the day. That listing arrives after the wave.
  - On 2026-11-20 it still promotes "Christmas / holidays (35 days)". Christmas
    printable sales peak mid-Oct → mid-Nov; a fresh no-review listing on Nov 20
    is late. Meanwhile on 2026-09-20 (the actual moment to build Christmas
    products) Christmas is 96 days out — OUTSIDE the 70-day window — so the
    system stays silent exactly when it should be loudest. **This is the
    "too late" half of Maj's complaint, mechanically.**
- **Fix:** per-event windows instead of one global 3–10 weeks. Add
  `lead_min_weeks`/`lead_max_weeks` per event in `_EVENTS`:
  - Christmas: 6–14 weeks (start ~mid-September).
  - Valentine's/Mother's/Father's/Easter/Halloween: 4–9 weeks.
  - New Year planners: 2–8 weeks (genuinely late-shopped).
  - Back to school: 4–10 weeks.
  Default `min` should never be below 4 weeks — below that a new listing can't
  rank in time, so building it is wasted spend.
- **Verify:** unit tests: on Sep 20 Christmas IS in window; on Nov 20 Christmas
  is NOT; on Apr 20 Mother's Day is NOT (≤3 weeks); docstring matches code.

### 1-3. Nothing FORBIDS out-of-season concepts — and the trend data actively feeds them (the "too early / too late" other half)
- **Where:** `app/services/trend_data_service.py:100` (`timeframe="today 3-m"`),
  `app/agents/market_intelligence/research.py` + `intelligence.py` (prompts
  contain NO date context at all), `app/agents/trend_research_agent.py:212-219`
  (`_seasonal_block` is only a soft "strongly prefer"),
  `_validate_product` (no seasonal check), `product_viability_critic.py`
  (critic never told today's date).
- **What:** Google Trends "today 3-m" is a TRAILING window: in July it still
  shows graduation/Father's-Day queries as top risers; in January it shows
  Christmas. Those poisoned queries flow into the research prompt, which has no
  idea what today's date is, then into the concept LLM, where the seasonal
  block merely *prefers* upcoming occasions — it never says "graduation JUST
  PASSED, do not build for it". No validator or critic can catch it either,
  because none of them receive the date. This is how a Halloween product gets
  proposed in December and a graduation product in July.
- **Fix (three cheap layers):**
  1. **Prompt, negative list:** extend `seasonal_prompt_block()` to also emit
     "occasions that recently passed or are too far out — do NOT propose
     products for these: {Father's Day (passed 3 weeks ago), Halloween (16
     weeks away), ...}", and inject the same dated block into
     `ResearchAgent.research()` so the analyst reads trend data with a
     calendar in hand.
  2. **Code gate (the reliable one):** new `app/core/seasonality.py`
     `occasion_mismatch(name, description) -> str | None` — keyword map per
     event (christmas, xmas, santa, valentine, easter, halloween, graduation,
     back to school, thanksgiving, mother's day, father's day, st patrick...);
     if the concept references an event whose window (per 1-2) does NOT include
     today, return a rejection reason. Call it in
     `TrendResearchAgent._validate_product` exactly like the trademark screen
     — the LLM gets retry feedback and proposes something else.
  3. **Critic context:** include `today` and the in-window occasions in the
     critique JSON so the critic can down-score mistimed products it happens
     to see (belt-and-braces).
- **Verify:** unit tests: a "Christmas Gift Tags Printable" concept is rejected
  on July 11 and accepted on October 10; a "Graduation Cap Print" is rejected
  on July 11. Live: watch one autonomy cycle log the negative block.

### 1-4. Seasonal listings never come DOWN — the shop accumulates out-of-season inventory
- **Where:** no code path deactivates listings by occasion;
  `app/services/listing_prune_service.py` exists but (a) is only reachable via
  manual `POST /admin/prune-listings` (never scheduled) and (b) only prunes by
  age ≥ 100 days + views ≤ 10.
- **What:** a Valentine's listing built in January stays active all year. A
  shop face full of dead-season products depresses overall conversion (buyers
  judge the shop grid), keeps paying $0.20 auto-renew every 4 months, and
  drags Etsy's shop-quality signal.
- **Fix:**
  1. When a concept passes the 1-3 gate with an occasion match, stamp
     `metadata_["occasion"] = <event key>` on the task and carry it to the
     listing record.
  2. Add a weekly tick (fold into `EtsyReceiptWorker` like `_maybe_backup`)
     that deactivates active listings whose occasion window closed
     (`update_listing(state="inactive")` already exists) and — the nice part —
     REACTIVATES them (`state="active"`) when the window reopens next year:
     the listing keeps its age/views/favorites, which beats rebuilding.
  3. Schedule `ListingPruneService.run(apply=False)` monthly in the same tick
     so dead-inventory reports actually happen (today the endpoint is never
     called).
- **Verify:** create a fake task with `occasion=valentines` + a stubbed Etsy
  client; assert deactivation fires after Feb 14 and reactivation fires in
  early January.

### 1-5. Trend signal has no DIRECTION — a fading keyword looks identical to a rising one
- **Where:** `app/services/trend_data_service.py:102-104` — only
  `interest_df[kw].iloc[-1]` (the latest weekly value, which Google often
  returns as a PARTIAL week, understating it) is kept.
- **What:** "graduation printable" in late June has a high last-value but is
  collapsing; "halloween printable" in August has a modest value but is
  climbing. The research prompt can't tell them apart — another mechanical
  source of too-late products.
- **Fix:** from the same DataFrame (no extra API calls), compute
  `interest_now` = mean of the last 4 full weeks, `interest_prev` = mean of the
  4 weeks before that, and `direction` = "rising/flat/falling" (e.g. ±20%
  threshold). Drop the final partial bucket. Emit all three per keyword in the
  payload; tell the research prompt to weight rising >> falling.
- **Verify:** unit test with a synthetic rising and falling series; check the
  payload marks them correctly.

### 1-6. One Google rate-limit ban silently halts ALL product creation
- **Where:** `app/services/trend_data_service.py:120-124` (fail-loud raise),
  cache TTL only 12h (`TREND_CACHE_HOURS`), `TrendResearchAgent.run():91-98`
  aborts the whole cycle on `TrendDataFetchError`.
- **What:** fail-loud (no invented data) is right, but pytrends scrapes an
  unofficial endpoint from one Railway IP; a multi-day 429 ban means ZERO new
  products for days and nothing pages Maj (it's just an error log per cycle).
- **Fix:** on fetch failure, fall back to the on-disk cache even if expired,
  up to a bounded staleness (e.g. 7 days), logging + alerting "serving stale
  trend data (N hours old)". Trends barely move week-to-week; stale-real data
  is far better than a halted factory. Past the bound, keep failing loud AND
  send one AlertService alert per day (today no alert fires at all).
- **Verify:** unit test: fetch raises + cache 3 days old → payload served with
  a `stale: true` marker; fetch raises + cache 10 days old → raises + alert.

### 1-7. (Smaller) Selection diversity: always `opportunities[0]`, always the same research topic
- **Where:** `app/agents/trend_research_agent.py:131` (`insight =
  opportunities[0]`), `_RESEARCH_TOPIC` fixed string.
- **What:** the intelligence agent returns ~3 opportunities; the code always
  takes the first, and every cycle researches the identical topic sentence —
  the attractor-concept problem (A-3 dedup fights the symptom).
- **Fix:** rotate/randomize among the top 3 opportunities; when an occasion is
  in-window (1-2), run every Nth cycle with the occasion itself as the research
  topic ("Etsy {occasion} printable products buyers want").
- **Verify:** log which opportunity index was used; watch concept variety over
  a week.

### 1-8. coloring pages are already precolored
- make an explicit rule that any generated coloring pages have to be comlpetely white
expect for the black lines that show where to color. There is no point in buying a coloring
page that has a half that is already colored

---

## TIER 2 — The learning loop learns the wrong things

### 2-1. "Best products" can be earned with ZERO sales — the insights block feeds noise back into concept generation
- **Where:** `app/services/best_products_service.py:20` (`MIN_SCORE_FOR_BEST =
  40`), `app/services/performance_service.py:24-31` (weights: revenue 50,
  reliability 30, engagement 20).
- **What:** a task with 0 retries (30 pts) + modest views (up to 20 pts)
  scores 40-50 with **zero revenue** and becomes a "best product". The A-1
  insights block then tells the concept LLM "Best-performing formats so far:
  phone_wallpaper (6)…" when phone wallpapers merely *didn't crash*.
  Reliability measures the pipeline, not the buyer — it has no place in a
  merchandising signal. The learning loop can currently converge on formats
  that got views but never sold.
- **Fix:** in `get_best_product_insights` (the learning-loop consumer only —
  keep the dashboard score as-is if desired): rank by revenue first; when no
  task has revenue yet, rank by engagement RATE (see 2-2) and label the block
  honestly: "No sales yet — formats with the most buyer views/favorites:".
  Never let reliability points qualify a product as "best". Also state
  explicitly in the block which formats have many listings and $0 revenue
  (the anti-signal), which today only appears as a neutral "shop mix" line.
- **Verify:** unit test: task with 0 sales + 0 views + 0 retries does NOT
  appear in insights; a task with 1 recorded sale outranks any zero-sale task.

### 2-2. Engagement uses LIFETIME cumulative views — old listings permanently dominate new winners
- **Where:** `app/services/performance_service.py:49-65` (latest
  `listing_stats` event = cumulative views), same value reused by
  `marketing_refresh_service.py:130-139` for re-promotion priority.
- **What:** Etsy's `views` field is lifetime. A 6-month-old mediocre listing
  with 90 views beats a 5-day-old listing pulling 15 views/day — in both the
  performance score and the "re-promote proven products harder" ordering. The
  loop over-promotes stale products and under-promotes fresh momentum.
- **Fix:** listing stats are already recorded DAILY (A-10). Compute
  velocity = (latest.views − previous.views) / days between events (fallback:
  views / listing age in days for the first event) and use velocity for both
  engagement score and refresh priority.
- **Verify:** unit test with two synthetic stat series (old+flat vs new+steep);
  assert the new one wins both rankings.

### 2-3. Winner-variant tasks skip the spend reservation and lose the parent's grounding
- **Where:** `app/workers/etsy_receipt_worker.py:581-612`
  (`_maybe_spawn_winner_variant`).
- **What:** (a) it checks the variant cap but never `can_spend()` — each
  variant costs ~$0.80 of image spend that was never reserved, so a sale late
  in the day can push past MAX_DAILY_SPEND_USD; (b) the new task's metadata
  carries none of the parent's `market`/`seo_context`, and for a
  `pdf_planner_or_guide` parent no `page_count` — if the executor returns no
  `sections`, `_resolve_pdf_page_briefs` (pipeline_orchestrator.py:1026-1047)
  silently produces a **1-page planner** listed as a planner.
- **Fix:** add `if not auto.can_spend(0.80): return` next to the variant-cap
  check; copy `market`, `seo_context`, and `page_count` from the parent task's
  metadata into the variant's metadata.
- **Verify:** extend the A-1 test: spend ledger at $4.90 → sale arrives → no
  variant task; PDF parent variant carries page_count.

### 2-4. Winning-title n-grams are collected but never reach the tags
- **Where:** `ListingGeneratorAgent._derive_tags` accepts `extra_terms`
  (app/agents/etsy/listing_generator.py:26-56) but the real call site
  `pipeline_orchestrator.py:1106` never passes it, even though
  `task.metadata_["market"]["top_titles"]` (A-2) is sitting right there.
- **What:** tag padding falls back to product-name fragments instead of
  phrases proven to rank for this exact niche. Tags are Etsy search — this is
  free relevance being discarded.
- **Fix:** extract 2–3-word n-grams from `top_titles` (filter through the
  trademark screen — competitor titles DO contain brand terms), pass as
  `extra_terms`.
- **Verify:** unit test: with market top_titles present, at least one derived
  tag comes from a title n-gram; trademarked n-grams excluded.

---

## TIER 3 — Distribution: built, but switched off

### 3-1. Pinterest is still not connected in production
- **Where:** pipeline skips at `pipeline_orchestrator.py:1415-1418`
  (`pinterest_connected()` false) — by design, but it's been false for weeks.
- **What:** Pinterest is the single highest-intent free channel for exactly
  this catalog (printables, wall art, planners). Every product ships with zero
  Pinterest distribution; Tumblr alone is weak. This is likely the biggest
  single traffic lever available and it's a 15-minute manual action.
- **Fix (Maj, manual):** create/verify the Pinterest business account, set
  `PINTEREST_APP_ID/SECRET/BOARD_ID` in Railway, complete
  `/pinterest/oauth/login`, optionally set `PINTEREST_BOARD_MAP` per format
  (A-9 supports it). Then confirm a new task posts a pin.

### 3-2. Marketing refresh is OFF — products get one social post ever, then silence
- **Where:** `MARKETING_REFRESH_ENABLED=false` (config/settings.py:207);
  worker + service are built, tested and capped (3 posts/6h, 7-day per-product
  interval, ~$0.0003/post).
- **Fix (Maj, manual):** set `MARKETING_REFRESH_ENABLED=true` in Railway. After
  2-1/2-2 land, refresh priority will also stop favoring stale listings.

### 3-3. The Pinterest pin image is a fresh $0.04 generation instead of a free existing mockup
- **Where:** `pipeline_orchestrator.py:1434-1438` →
  `PinterestImageService.enrich_listing_with_image` (generates a new image per
  task).
- **What:** the pipeline already built 4 free PIL listing mockups of the REAL
  design (step 2.6). Generating an independent pin image costs money AND
  reintroduces the "marketing image differs from the product" risk the
  consistency gate exists to kill.
- **Fix:** compose the pin from an existing listing mockup (Pinterest's ideal
  is 2:3 — `MockupService` can render a 1000×1500 variant); fall back to
  generation only if no mockup exists.
- **Verify:** run a task with Pinterest stubbed-connected; assert no
  image-provider call is made for the pin.

### 3-4. Etsy listing videos (deferred B-6) — cheap deterministic win when ready
- **What:** Etsy boosts listings with video (and buyers convert better). A
  5–10s ken-burns pan/zoom over the verified design is deterministic
  (PIL/ffmpeg), zero image-gen; Etsy accepts video upload via API
  (`uploadListingVideo`).
- **Fix:** later step: render + upload one video per listing; reuse for
  Pinterest video pins.

---

## TIER 4 — Money math honesty

### 4-1. P&L overstates profit: renewal, transaction and payment fees are invisible
- **Where:** spend ledger records image/vision cost + the one-time $0.20
  listing fee (`pipeline_orchestrator.py:1204-1210`); D-6 P&L = revenue −
  that ledger. Etsy's ~6.5% transaction fee + ~3%+$0.25 payment processing +
  $0.20 auto-renew per listing per 4 months + possible 12-15% offsite-ads fee
  never appear.
- **What:** at scale the gap is material: 300 listings ≈ $180/year in renewals
  alone; a $4 digital sale nets ~$3.30 not $4. The dashboard tile (and the
  learning loop's "Total recorded revenue" prompt line) run on gross numbers.
- **Fix:** two options, do (1) now: (1) record a computed
  `fee_estimate` event per sale (6.5% + 3% + $0.25) and per renewal window;
  (2) better, poll Etsy's real payment-account ledger
  (`GET /shops/{shop_id}/payment-account/ledger-entries`) daily in the
  receipt worker and record actual fees. Show gross AND net in the P&L
  endpoint.
- **Verify:** unit test the fee math on one synthetic sale; compare a week of
  ledger entries against the Etsy dashboard by hand once.

### 4-2. Charm pricing: everything publishes at flat band midpoints
- **Where:** `app/core/product_formats.py:110-116` (`clamp_price` midpoint,
  e.g. $5.75/$3.25), market median override at
  `pipeline_orchestrator.py:1171`.
- **What:** Etsy digital buyers respond to $X.99/$X.49 anchors; midpoints like
  $5.75 look arbitrary. Zero-cost conversion lever.
- **Fix:** after clamping/market-grounding, snap to the nearest .99 or .49
  ending within the band (`snap_charm(price)` helper in product_formats.py).
- **Verify:** unit tests: 5.75→5.99 (in band), 8.00 band-cap→7.99.

### 4-3. Volume vs. quality is currently tuned for volume with no evidence
- **Where:** `MAX_TASKS_PER_DAY=10`, `VIABILITY_CRITIC_MIN_SCORE=6`.
- **What (business judgment, not a bug):** 10 unproven products/day ≈ $2/day
  listing fees + ~$3/day image spend on a shop with (as of this audit) little
  recorded revenue. A new Etsy shop's early conversion signal is diluted
  across many mediocre listings; Etsy's algorithm also reads shop-level
  conversion. Fewer, better products likely earn more until the first
  consistent sales arrive.
- **Fix (Maj decision, env-only):** consider `MAX_TASKS_PER_DAY=5` +
  `VIABILITY_CRITIC_MIN_SCORE=7` + `CONCEPT_MODEL=anthropic/claude-sonnet-5`
  (B-5 knob exists; concept quality is the highest-leverage LLM output in the
  system and costs cents/day). Revisit after 30 days of stats.

---

## TIER 5 — Technical robustness

### 5-1. Daily spend/task ledger has a read-modify-write race across threads
- **Where:** `app/services/autonomy_service.py:39-49` — `_load()` → mutate →
  `_save()` with no lock; writers live on different threads (TaskWorker via
  the image provider `openrouter_image_provider.py:114-119`, EtsyReceiptWorker
  via listing fees/variants, AutonomyWorker via task counts).
- **What:** two concurrent `record_spend` calls can drop one increment — the
  cap under-counts real spend. Low probability per event, but this file IS the
  wallet guard.
- **Fix:** a module-level `threading.Lock` around load-mutate-save, plus
  atomic write (write temp file, `os.replace`). ~10 lines.
- **Verify:** unit test hammering `record_spend` from 8 threads × 50 calls;
  final total must equal 400 × amount.

### 5-2. Per-image spend is recorded but never GATED — the cap is advisory mid-task
- **Where:** `openrouter_image_provider.py:114-119` records after generating;
  only `AutonomyWorker._run_cycle` (autonomy_worker.py:112-118) checks
  `can_spend`, once, with a $0.80 estimate.
- **What:** a pathological task (remakes + QA retries + PDF pages) can spend
  multiples of its reservation after the cap is already exhausted. The system
  currently has no true circuit breaker on the wallet.
- **Fix:** in the provider, before generating: if
  `spend > MAX_DAILY_SPEND_USD * 1.5` (grace factor so an in-flight task can
  finish), raise a clear `SpendCapExceeded` — the pipeline's existing failure
  handling blocks the task loudly.
- **Verify:** unit test: ledger primed over 1.5×cap → provider refuses.

### 5-3. pytrends is an unofficial scraper pinned to nothing
- **Where:** `requirements` / `pyproject` (verify the pin), pytrends'
  `related_queries()` return shape has changed across releases before.
- **What:** an unpinned upgrade or a Google frontend change turns into
  `TrendDataFetchError` every cycle (see 1-6 for the blast radius).
- **Fix:** pin the exact working version; add a tiny weekly canary (one
  keyword fetch) that alerts on failure so breakage is noticed same-day, not
  when someone reads logs.

### 5-4. Repo housekeeping
- Branch `audit-step102-fixes` is the live working branch with unmerged
  step-103 commits; `instructions/AUDIT_STEP103_growth_and_revenue_audit.md`
  and `scripts/test_step103_d2b_d6.py` are still untracked. Commit them, merge
  to `main`, and let the new CI (D-5) run on main. Two test scripts also carry
  a stray 1-line diff (line endings) — commit or checkout.

---

## TIER 6 — Still-open manual actions (Maj, mostly Railway env / accounts — none need code)

Consolidated from steps 102/103 notes; several are security-relevant and
predate this audit. Check each off:

1. **Rotate the Etsy + Tumblr OAuth tokens** — they appeared in git history
   (step 102 finding). Until rotated, treat them as leaked. (Security, do first.)
2. **Set `FACTORY_API_KEY`** in Railway if not already set — without it every
   money-spending POST endpoint is open (enforcement is off when unset,
   `app/api/auth.py` / settings.py:72).
3. **Configure `BACKUP_S3_*`** (Cloudflare R2 / Backblaze B2) — backups
   currently live only on the same Railway volume they protect.
4. **Connect Pinterest** (see 3-1) and **enable `MARKETING_REFRESH_ENABLED`**
   (see 3-2).
5. **Add Printify as a production partner** in Etsy Shop Manager and set
   `ETSY_PRODUCTION_PARTNER_ID` — required for POD compliance the moment POD
   is re-enabled (C-2).
6. **Create shop sections** (`scripts/create_shop_sections.py`) and set
   `SHOP_SECTION_MAP` so listings stop landing unsectioned (B-7 support is
   already in the pipeline).
7. **Run `scripts/audit_existing_listings.py`** against the live shop —
   pre-fix listings may still carry old taxonomy/when_made/pricing defects.
8. **Shop-level conversion surfaces** (one-time, ~1h): banner, about section
   with the AI-assisted disclosure mirrored, shop announcement, policies/FAQ,
   featured listings. The API can't do these; a bare shop face suppresses
   conversion on every listing at once.
9. **Monthly: run an Etsy sale/coupon** (not exposed via API v3) — a 20-25%
   sale event is Etsy's strongest built-in urgency lever, especially before
   each occasion window (pairs with Tier 1).

---

## TIER 7 — Product-breadth backlog (deliberate growth, after Tiers 1-3)

1. **Wall-art SETS as an explicit format** (deferred STEP 103 B-3
   `wall_art_set_3`): sets of 3 coordinated prints are the highest-AOV digital
   listing type on Etsy ($8-15 vs $3-6), and the multi-item validator
   currently (correctly) bans "set/bundle" wording for everything else. Add a
   real `wall_art_set_3` format (3 generated pieces sharing one palette/theme,
   one listing, 3+ delivery files via the existing bundle service) with a
   format-scoped exemption from `_MULTI_ITEM_MARKERS`, consistency-gated
   across the 3 pieces.
2. **Re-enable POD with real variants** (B-1(a)): a POD tee nets ~$6/sale vs
   ~$3-5 digital, but stays off until buyers can pick sizes/colors
   (`POD_APPAREL_ENABLED=false` is correct today). Build Printify
   variant-array → Etsy variations mapping, then flip the flag.
3. **Seasonal seed expansion:** `SEED_KEYWORDS`
   (trend_data_service.py:27-36) covers evergreen categories only; the
   seasonal fold-in adds max 2. After Tier 1 lands, let each in-window
   occasion contribute its 2-3 proven phrases ("christmas gift tags
   printable", "teacher appreciation printable", "wedding welcome sign") —
   config-only via `TREND_SEED_KEYWORDS` once validated.
4. **Listing SEO refresh for zero-view listings:** after 21+ days with <5
   views, rewrite title/tags once using current `EtsyMarketService.top_titles`
   (update_listing PATCH already exists). One retry per listing, logged —
   turns dead listings into second chances for ~$0.001 of LLM.

---

## Suggested working order

| Step | Items | Type |
|------|-------|------|
| 104-A | 1-1, 1-2, 1-3 (seasonality engine: real dates, per-event windows, hard gate + dated prompts) | code |
| 104-B | 1-4 (seasonal listing lifecycle) + schedule prune | code |
| 104-C | 2-1, 2-2 (fix learning-loop signals) | code |
| 104-D | 1-5, 1-6 (trend direction + stale-cache fallback + alert) | code |
| 104-E | 2-3, 2-4, 3-3, 4-2 (small: variant guardrails, tag n-grams, pin reuse, charm pricing) | code |
| 104-F | 4-1 (real P&L: fees) | code |
| 104-G | 5-1, 5-2, 5-3, 5-4 (robustness + housekeeping) | code |
| 104-H | Tier 6 checklist | manual (Maj) |
| 104-I | Tier 7 backlog, starting with wall-art sets | code (bigger) |

Everything in Tiers 1–5 is testable offline (the repo's deterministic-suite
pattern in `tests/test_deterministic_suites.py` fits all of it); nothing
requires new paid APIs.
