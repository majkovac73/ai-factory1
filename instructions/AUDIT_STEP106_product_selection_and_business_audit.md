# STEP 106 — Product Selection & Business Audit (2026-07-13)

Fifth full audit. Trigger: **since the ≥95 ProductScoreService gate shipped
(STEP 105, deployed 2026-07-12), ZERO new products have been created.** Maj's
brief: (1) find out why and fix product FINDING — do NOT lower quality
standards; (2) make the search persistent ("try until it finds a suitable
product", not once per cycle); (3) audit the whole product-choice + build
logic; (4) audit the business/money side and tune it for maximum profit;
(5) log everything, even small stuff. Goal state after working this file:
no real issues left, runs on its own, makes money.

Ordered most → least important. Each finding: WHAT / WHERE / WHY / HOW to fix
/ VERIFY. Work top-down as separate steps with Claude Code. Tier 0 is
diagnosis (do it first, ~15 min, no code). The headline is finding **1-1: the
95 bar is mathematically unreachable — it is not "strict", it is broken.**

Production reality assumed (from STEP 105): AUTONOMY_ENABLED=true,
AUTO_PUBLISH_LISTINGS=true, MAX_TASKS_PER_DAY=10, MAX_DAILY_SPEND_USD=5,
Railway + volume, Tumblr connected, Pinterest NOT connected, POD paused,
WALL_ART_SET / LISTING_VIDEO / MARKETING_REFRESH all off.

---

## HOW PRODUCT SELECTION RUNS TODAY (so the fixes below make sense)

One cycle per hour (`AutonomyWorker._run_cycle`, `app/workers/autonomy_worker.py:96`):

1. Caps checked: `MAX_TASKS_PER_DAY` (10), `can_spend($0.80)`.
2. `TrendResearchAgent.run()` (`app/agents/trend_research_agent.py:93`):
   - Pull real Google Trends data (12h cache, stale-fallback up to 7 days).
   - ResearchAgent LLM call → market findings text.
   - IntelligenceAgent LLM call → 3 "opportunities".
   - Pick **ONE** opportunity at random from the top 3.
   - `_propose_product()`: up to **5 attempts** against that ONE insight.
     Each attempt: concept LLM call → schema/strategy/multi-item validators →
     trademark screen → seasonality gate → dedup vs shop → attach real Etsy
     market data → **ProductScoreService.score()** (2 judge LLM calls) →
     - `PRODUCT_SCORE_ENFORCE=true`: pass iff total ≥ 95, else retry w/ feedback.
     - `PRODUCT_SCORE_ENFORCE=false` (shadow): score only recorded; the OLD
       6/10 critic (a 3rd LLM call) decides.
3. If all 5 attempts fail (or research/trends/opportunities fail): **the cycle
   creates nothing and the factory sleeps until the next hour.** No other
   opportunity is tried, no alert fires, nothing is visible unless you read logs.

Score composite (`app/services/product_score_service.py`):
`total = B + C`, where B = deterministic evidence 0-40 (demand 10, competition
10, price 10, timing 5, originality 5) and **C = 6 × min(judge_a, judge_b)**,
each judge being the 1-10 viability-critic rubric on `CONCEPT_MODEL` and
`DEFAULT_MODEL`.

---

## TIER 0 — Diagnose the live zero-production (do FIRST, no code)

### 0-1. Read the Railway env — which mode is the gate actually in?
- **What:** The repo defaults are `PRODUCT_SCORE_ENFORCE=false` (shadow) and
  `CONCEPT_MODEL` unset. If Maj flipped `PRODUCT_SCORE_ENFORCE=true` early,
  zero products is fully explained by finding 1-1 (bar unreachable). If it's
  still false, note that setting `CONCEPT_MODEL=anthropic/claude-sonnet-5`
  ALSO changed the deciding shadow-mode critic (`TrendResearchAgent.__init__`
  builds `self._critic` with CONCEPT_MODEL, trend_research_agent.py:80) — a
  stronger, harsher judge model can push previously-passing concepts under the
  6/10 bar too.
- **How:** In Railway → Variables, record the current values of:
  `PRODUCT_SCORE_ENFORCE, PRODUCT_MIN_SCORE, CONCEPT_MODEL, SEO_MODEL,
  AUTONOMY_ENABLED, AUTONOMY_INTERVAL_MINUTES, AUTO_PUBLISH_LISTINGS,
  MAX_TASKS_PER_DAY, MAX_DAILY_SPEND_USD`.
- **Verify:** Values written down before touching code, so after-fix behavior
  changes are attributable.

### 0-2. Pull the concept_scored distribution — the calibration dataset exists
- **What:** Every scored concept since the deploy is already recorded.
- **How:** `GET /analytics/events?event_type=concept_scored&limit=500`
  (route exists, `app/api/routes/analytics.py:19`). For each event note
  `value` (total), `payload.judges.concept_model.score`,
  `payload.judges.default_model.score`, `payload.deterministic.total`,
  `payload.passed`.
- **Expected (per finding 1-1):** totals clustered ~55-85, `passed=false` on
  every single one, no judge pair at 10/10. If instead there are NO events at
  all, the cycles are dying earlier (trends/research/opportunities) — check 0-3.
- **Verify:** You can state "N concepts scored, max total X, judge max (a,b)".

### 0-3. Grep the Railway logs for the failure shape
- **How:** Search recent logs for these exact strings:
  - `product score=` (scores per attempt, incl. judge pair)
  - `below the 95 bar` (enforce-mode rejections)
  - `failed viability critique` (shadow-mode rejections)
  - `could not produce a specific, valid, buildable` (all 5 attempts burned)
  - `returned no opportunity, skipping` (cycle died pre-concept)
  - `error in cycle` (exceptions — e.g. an invalid CONCEPT_MODEL id makes the
    shadow critic raise, which kills the whole cycle; the score-service judges
    swallow errors as score 0, trend_research_agent.py:244 has no try/except)
  - `daily task cap` / `spend cap` (caps, unlikely)
- **Verify:** You know which of the three failure modes is live: (a) enforce
  on + nothing ≥95, (b) shadow + harsher critic model failing everything,
  (c) exceptions killing cycles.

---

## TIER 1 — Fix product FINDING (keep the bar strict, make it reachable and persistent)

### 1-1. THE BUG: 95/100 is mathematically unreachable — recalibrate without loosening
- **Where:** `app/services/product_score_service.py:224-228`,
  `config/settings.py:280` (`PRODUCT_MIN_SCORE=95`).
- **What's wrong (the arithmetic):**
  - LLM points `C = 6 × min(judge_a, judge_b)`, max 60.
  - Deterministic `B` max 40 — and a REAL max closer to 33-38: demand 10
    requires a *rising* matched seed keyword; competition 10 requires <1,000
    rivals (rare for any honest niche query); price 10 requires p50 in the
    band's upper half.
  - To reach 95: even with a PERFECT B=40, C must be ≥55 → `min(judges) ≥
    9.17` → **both judges must return 10/10** (integers). With realistic
    B=36, still both-10s. The judges' own rubric
    (`product_viability_critic.py:118-125`) calls 8-10 "distinctive and
    clearly compelling" — LLM judges essentially never emit dual 10s, and the
    harsher-of-two rule makes it doubly impossible.
  - Even STEP 105's own calibration expectation ("obvious winners land 90+")
    can't happen: B=38 + dual 9s = 92 is the practical ceiling.
  - Conclusion: with enforce on, the factory builds nothing, forever, by
    construction. An unreachable bar is not strictness — it selects nothing,
    so the shop's average quality is frozen at its current 4.1/10.
- **Fix (recommended — keeps "excellent on every axis" semantics, arguably
  STRICTER than today because it puts explicit floors on each axis instead of
  one blended number):**
  1. Keep the composite exactly as is (B + 6×min).
  2. Change the pass rule in `ProductScoreService.score()` from a single
     `total >= min_score` to **total ≥ PRODUCT_MIN_SCORE AND three floors**:
     - `harsher judge ≥ 9` (both judges agree it's in the top "distinctive
       and clearly compelling" band — this is the real quality bar);
     - `weaker-judge ≥ 8` is implied by harsher=min, so instead:
       `max(judge_a, judge_b) ≥ 9` comes free; add `deterministic total ≥ 30/40`
       (evidence must be strong, not just judges enthusiastic);
     - no deterministic axis at its floor value (demand > 0 i.e. not falling,
       competition > 2 i.e. not >50k-saturated, originality > 1).
  3. Set the default `PRODUCT_MIN_SCORE=90` (env knob unchanged, Maj can push
     it later WITH data). Reachable example: B=36 (rising keyword, <10k
     rivals, p50 upper half, evergreen, original) + dual 9s = 90. That is a
     genuinely excellent concept — far above anything the old 6/10 critic
     passed — and the floors mean 90 can't be reached by e.g. B=40 + judges 8/9.
  4. Keep recording `concept_scored` events unchanged (the floors go into the
     payload too: `payload.floors={judge:bool, det:bool, axis:bool}`).
  5. Add a REGRESSION TEST that the bar is reachable: fixture with det=36 +
     stubbed dual-9 judges → passes; det=40 + judges 8/10 → fails (judge
     floor); det=28 + dual 10s → fails (det floor); falling-demand axis →
     fails (axis floor). This test is the guard against ever re-shipping an
     impossible gate.
- **Verify:** Unit tests above; then live shadow data (0-2) re-scored against
  the new rule shows a plausible pass rate (expect roughly 0-3 passes/day —
  the STEP 105 design target).

### 1-2. Persistent search — "try until it finds a suitable product" (Maj's explicit ask)
- **Where:** `TrendResearchAgent.run()` (trend_research_agent.py:161-172),
  `AutonomyWorker._run_cycle` (autonomy_worker.py:126-130).
- **What's wrong:** A cycle picks ONE random opportunity of the 3 the
  intelligence step returns, gives it 5 concept attempts, and gives up until
  next hour. The other 2 opportunities are thrown away. A failed cycle
  produces nothing and nothing retries. At 24 cycles/day that's at most 24
  insights ever explored, 1/hour, with ~2/3 of generated opportunities never
  tried.
- **Fix (bounded persistence, not an infinite loop):**
  1. In `run()`, iterate over ALL returned opportunities (shuffle the top 3,
     then try each in turn): `for insight in opportunities: product =
     _propose_product(insight, ...); if product: return product`.
  2. If all opportunities fail, do ONE fresh research pass with a different
     research topic (e.g. force an in-window occasion topic if the first was
     generic, or the next seed category) and try its opportunities too.
  3. Add a hard total budget so cost stays bounded:
     `CONCEPT_SEARCH_MAX_ATTEMPTS_PER_CYCLE` (settings, default 15 concept
     attempts ≈ 30 judge calls) and stop when hit. Log
     `TrendResearchAgent: cycle exhausted N attempts across M insights; best
     score X` — the best-score line is the tuning signal.
  4. JSON-parse failures and schema rejects should consume a CHEAP retry
     budget, not the same counter as fully-scored attempts (today a bad JSON
     response burns one of 5 attempts before any judging happens,
     trend_research_agent.py:196-198). Track `scored_attempts` separately from
     `raw_attempts` (cap raw at ~2× scored).
- **Cost note:** worst case/cycle ≈ 15 concept calls + 30 judge calls. On
  gpt-4o-mini that's cents/day. If `CONCEPT_MODEL=sonnet-5`, judges get
  pricier — see 1-8 (meter it) before raising the caps.
- **Verify:** New unit test: stub scorer that fails insight #1's concepts and
  passes one from insight #2 → run() returns a product (today it returns
  None). Live: log line shows multiple insights tried in one cycle.

### 1-3. Best-of-pool selection instead of first-past-the-post
- **Where:** `_propose_product` returns the FIRST concept that clears the bar.
- **What:** Once the bar is reachable (1-1), the first passer isn't
  necessarily the best of the cycle. All candidates are already scored — keep
  the breakdowns, and if ≥2 candidates pass within the cycle's attempts,
  build the highest-scoring one. Zero extra spend (scores already computed);
  strictly better selection.
- **How:** Collect `(concept, score)` for every scored attempt; short-circuit
  early only when a concept scores ≥ (PRODUCT_MIN_SCORE + 5); otherwise
  finish the attempt budget and pick the max passer.
- **Verify:** Unit test with stubbed scores [91, 96] → the 96 concept is
  returned even though 91 passed first.

### 1-4. Demand subscore is blind to the specific niche — match rising queries too
- **Where:** `ProductScoreService._demand`
  (product_score_service.py:55-73), trend data from
  `TrendDataService` (rising_queries + interest_trend).
- **What's wrong:** Demand is scored ONLY by token overlap between the concept
  name/description and the ~8 generic SEED keywords ("printable wall art",
  "coloring pages"...). A genuinely hot niche concept sparked by a specific
  RISING QUERY (e.g. "capybara coloring page" rising under "coloring pages")
  scores demand from the generic seed's direction — or 4/10 "no matching
  trend keyword" if it doesn't share a token with any seed. This biases the
  gate toward concepts that parrot seed-category words and PENALIZES exactly
  the specific, original concepts the judges reward — the two halves of the
  score fight each other.
- **Fix:** In `_demand`, also check `trend_data["rising_queries"]`: if the
  concept's tokens overlap a rising query phrase (2+ shared tokens or the
  full phrase as substring), score 10 with reason "matches rising query
  '<q>'". Seed-direction matching stays as fallback. Feed the same signal
  forward: when a rising query matched, put it into the concept task's
  `seo_context` so tags/title use it.
- **Verify:** Unit test: concept "Capybara Coloring Book Page" +
  rising_queries {"coloring pages": ["capybara coloring page"]} → demand 10.
  Today it returns 6 (flat seed) or 4.

### 1-5. Competition/price data queries the FULL product name — numbers are junk
- **Where:** `TrendResearchAgent._attach_market`
  (trend_research_agent.py:408-426) → `EtsyMarketService.validate_concept`.
- **What's wrong:** The Etsy search phrase is the whole product name, e.g.
  "Woodland Dreams Nursery Animal Print Set". Long phrases return tiny
  counts → `competition_count < 1000` → competition scores 10/10 while the
  REAL niche ("nursery animal print") has 50k+ rivals. Same distortion for
  price percentiles (computed over whatever few listings matched). The
  deterministic evidence the whole gate leans on is systematically inflated.
- **Fix:** Normalize the query before searching: lowercase, strip stopwords +
  filler ("printable", "digital", "instant", "download"), keep the first 3-4
  content tokens — the same normalization
  `ListingSeoRefreshService._query_from_listing` already implements
  (listing_seo_refresh_service.py:57-66); extract it into a shared helper.
  Store the query used in `market["query"]` for auditability. Consider a
  second lookup on the 2-token head niche and take the LARGER count
  (conservative).
- **Verify:** Unit test on the normalizer; manual spot-check: run the
  service for one long product name and confirm count changes materially vs
  the normalized query.

### 1-6. The "two independent judges" are the same model unless CONCEPT_MODEL is set
- **Where:** `ProductScoreService.__init__` (product_score_service.py:42-45).
- **What's wrong:** With `CONCEPT_MODEL` unset, judge_a = judge_b =
  `DEFAULT_MODEL` (gpt-4o-mini). `min()` of two samples from the SAME model
  isn't independent judgment — it's a random downward bias of ~0.5-1 point,
  which matters a lot at a 9-floor.
- **Fix:** (a) Maj sets `CONCEPT_MODEL=anthropic/claude-sonnet-5` in Railway
  (the knob exists; concept + judging is the highest-leverage LLM spend in
  the system, ~$1-3/day worst case with 1-2's caps). (b) In code, if the two
  resolved judge models are identical, log a WARNING once ("judges are not
  independent — set CONCEPT_MODEL") so the config gap is visible.
  (c) Thread `temperature=0.2` through the judge calls — `BaseAgent._generate`
  (base_agent.py:20-54) currently passes no temperature at all, so judges run
  at the provider default and re-scoring the same concept is noisy. Add an
  optional `temperature` param to `_generate` → `provider.generate`.
- **Verify:** Log line shows two distinct model ids per score; re-scoring one
  fixed concept 3× varies ≤1 point per judge.

### 1-7. Retry feedback forgets earlier rejected concepts in the same cycle
- **Where:** `_propose_product` feedback string (trend_research_agent.py:181+).
- **What:** Only the LAST rejection reason is fed back. Attempt 4 can happily
  re-propose something nearly identical to attempt 1 (only exact-shop dedup
  catches it). Wastes scored attempts.
- **Fix:** Accumulate a short list of this cycle's rejected names + one-line
  reasons + scores, and inject into the retry prompt: "Already rejected this
  cycle (do NOT propose variations of these): …". Cap at ~8 lines.
- **Verify:** Unit test: after 2 rejections the built prompt contains both
  rejected names.

### 1-8. Text-LLM spend is completely unmetered — the ledger only counts images
- **Where:** `AutonomyService` ledger; spend recorded only in
  `OpenRouterImageProvider` (images, $0.04) and vision-QA ($0.002).
- **What:** Research + intelligence + concept + 2 judges (+ shadow critic) =
  6-8 text calls per attempt-cycle today; with 1-2's persistence and sonnet
  judges this becomes real money that the $5/day cap and circuit breaker
  cannot see. The breaker protects images only.
- **Fix:** Record a flat per-call estimate at the text-provider choke point
  (`ProviderManager` provider `.generate`), e.g. `TEXT_LLM_COST_USD=0.002`
  default and `TEXT_LLM_COST_USD_STRONG=0.01` when the model id contains
  "sonnet"/"gpt-4o" (not mini). Same pattern as P0-13. Include text calls in
  `assert_within_circuit_breaker()` coverage (they should call it too before
  generating — today only image calls refuse past the ceiling).
- **Verify:** Unit test: a generate() call adds spend; daily_status shows
  text spend after one autonomy cycle in a dev run.

### 1-9. Silent zero-production — nothing tells Maj the factory made nothing
- **Where:** nowhere (that's the finding). AutonomyWorker logs INFO lines only.
- **What:** The current incident (days of zero products) was only noticed by
  a human looking at the shop. Alerting exists for spend caps, dead workers,
  stale trends — but not for the single most important business fact.
- **Fix:** Daily tick (fold into `EtsyReceiptWorker._maybe_seasonal_lifecycle`
  pattern or a new `_maybe_production_check`): if `AUTONOMY_ENABLED` and
  tasks_created==0 in the last 24h ledger
  (`autonomy_state_<date>.json`), send a Discord alert including the day's
  best `concept_scored` total and its weakest axes ("factory produced 0
  products today; best candidate scored 87: competition 5/10 — 23k rivals;
  judge 8/10 …"). Also add a small dashboard tile: products created last
  7 days + best score today (`/dashboard/overview` already aggregates).
- **Verify:** Unit test on the check function with a fixture ledger; force it
  once in dev to see the Discord message.

### 1-10. Near-miss queue — strictness without starvation
- **Where:** new, small; enforce-mode `_propose_product`.
- **What:** With a strict bar there will be days where the best concept
  scores 88-89. Today it's discarded silently. Give Maj the option, not the
  machine: track the day's best FAILED concept (score ≥ PRODUCT_MIN_SCORE-5),
  include it in the 1-9 daily alert, and add
  `POST /tasks/approve-concept` that accepts the logged concept JSON and
  creates the task (reusing AutonomyWorker's task-creation body). Human
  approval bypasses the gate explicitly and auditable — the autonomous bar
  itself never moves.
- **Verify:** Endpoint test: posting a concept JSON creates a task with
  `metadata.source="manual_approval"`.

---

## TIER 2 — Product quality / build-path logic (smaller, still worth doing)

### 2-1. Seamless-pattern tiling check is log-only — the product promise isn't enforced
- **Where:** `PipelineOrchestrator._stage_pod_design`
  (pipeline_orchestrator.py:1048-1056), `app/core/seamless.py`.
- **What:** "SEAMLESS repeating pattern" is the literal product. Edge mismatch
  >22 only logs a warning and ships anyway — a visibly-seamed "seamless"
  pattern is a refund/1-star generator.
- **Fix:** On mismatch >22, regenerate once (same brief + "must tile
  perfectly across all four edges"); if still failing, block the task like
  any content-QA failure. One extra $0.04 worst case, only for this format.
- **Verify:** Unit test stubbing edge_mismatch [30, 12] → one regen, passes;
  [30, 30] → blocked.

### 2-2. Wall-art-set palette mismatch is soft (matters once 3-1 enables the format)
- **Where:** `_stage_wall_art_set` (pipeline_orchestrator.py:1135-1139).
- **What:** A clashing 3-piece "coordinated set" ships with only a log line.
- **Fix:** If `palette_consistent=false`, regenerate the single outlier piece
  (max distance from the other two) once with "match the palette of the
  other pieces: <dominant colors>" feedback; still inconsistent → block.
- **Verify:** Unit test with stubbed distances.

### 2-3. Watermark more preview-is-the-product formats
- **Where:** `WATERMARK_FORMATS` (settings.py:287) =
  `["coloring_page", "phone_wallpaper"]`.
- **What:** A `sticker_sheet_design` or `seamless_pattern` mockup also shows
  ~the whole deliverable; a clean screenshot is a free copy.
- **Fix:** Add both to the default list (env-overridable already).
- **Verify:** Existing watermark tests extended for the two formats.

### 2-4. Executor title guidance says "pack 120-140 chars" — tune for 2026 Etsy search
- **Where:** `app/core/agents/executor.py:41-44`.
- **What:** Etsy's current guidance and ranking behavior weight the FIRST
  ~40 characters and penalize unreadable keyword soup (buyers see a truncated
  title in the grid). Max-stuffing to 140 was the 2020 meta.
- **Fix:** Rewrite guidance: first 40 chars = primary buyer phrase, clean and
  human-readable; then 2-3 complementary long-tails separated by "|"; total
  80-130 chars. Keep the 13-tag rule (that part is right).
- **Verify:** Spot-check 3 generated titles read naturally in the first 40 chars.

### 2-5. `_validate_product` requires the description to contain the exact name
- **Where:** trend_research_agent.py:488-489.
- **What:** `name.lower() not in description.lower()` forces the LLM to
  restate the full name verbatim, which then leaks into descriptions as
  awkward copy and burns retries when the model paraphrases ("the Plant
  Parent planner" vs "Plant Parent Weekly Care Planner"). Low value — the
  name is already carried separately everywhere.
- **Fix:** Relax to "≥2 significant name tokens appear in the description"
  (reuse `_tokens`).
- **Verify:** Unit test: paraphrased description passes; unrelated one fails.

### 2-6. Shadow-mode cycles pay for 3 judgments per attempt
- **Where:** trend_research_agent.py:220-259.
- **What:** In shadow mode every attempt runs 2 score-service judges + the
  deciding critic = 3 paid calls. Fine short-term; pointless once 1-1 ships
  and enforce flips.
- **Fix:** Once `PRODUCT_SCORE_ENFORCE=true` is stable (after 3-5 days of
  data under the new rule), delete the shadow branch entirely (the old
  critic stays only as the judge rubric inside ProductScoreService).
- **Verify:** grep: no `self._critic.critique` call left in
  trend_research_agent.py.

### 2-7. `occasion_for` stamps only the FIRST matching event — ordering trap
- **Where:** `app/core/seasonality.py:217-224`; `_EVENTS` ordering comment
  (nye before new_year) shows the fragility.
- **What:** A "Christmas gratitude planner" matches whichever of
  thanksgiving/christmas comes first in the table, not the more specific
  phrase. Wrong occasion stamp → wrong seasonal deactivation window.
- **Fix:** Match ALL events; if >1 hit, prefer the one whose matched keyword
  is LONGEST (more specific). Small pure-function change + tests.
- **Verify:** Unit: "christmas gratitude gift" → christmas, not thanksgiving.

### 2-8. PDF cover is the only image-generated page but gets no dedicated QA loop budget note
- **Where:** `PDFGenerationService` (per-page QA exists), settings
  `PLANNER_RENDER_INTERIOR=True`.
- **What:** Working as designed — logged here only for completeness: interior
  pages deterministic (good), cover image-gen w/ per-page QA (good). No action.

---

## TIER 3 — Business & money (make the most out of what already works)

### 3-1. Flip the free/cheap growth levers — one at a time, in this order
- **Where:** Railway env. All code shipped and tested in STEPS 103-105.
- **What/How:**
  1. `LISTING_VIDEO_ENABLED=true` — deterministic ken-burns MP4 per listing;
     Etsy ranks listings with video higher and converts better. Cost: CPU
     seconds. Watch one publish in logs (`listing video uploaded`).
  2. `MARKETING_REFRESH_ENABLED=true` — re-promotes the existing catalog on
     Tumblr every 6h, ≤3 posts/cycle, ≥7d per product. Cost ≈ $0.0003/post.
     **Do 3-2 FIRST** (asset pruning bug) or most refresh posts will no-op.
  3. `WALL_ART_SET_ENABLED=true` — the highest-AOV digital format ($8-15
     band vs $3-6). ~3× image cost per product (~$0.12-0.25). Enable after
     2-2 (palette enforcement) and watch the first set end-to-end.
  4. POD (`POD_APPAREL_ENABLED=true`) — LAST, and only after
     `ETSY_PRODUCTION_PARTNER_ID` is set (Etsy compliance, settings.py:107)
     and one canary POD product is verified with real size/color variations
     (7-2 shipped the code). POD has the highest revenue-per-sale ($6 target
     margin) but also the only real fulfillment risk.
- **Verify:** After each flip: one product/post/set observed live + no new
  alert noise for 48h before the next flip.

### 3-2. BUG: image auto-pruning starves the marketing-refresh loop
- **Where:** `ImageCleanupService` config (settings.py:152-154 — listing
  mockups deleted after 6 HOURS, delivery files after 3 days) vs
  `MarketingRefreshService._pick_asset_path`
  (marketing_refresh_service.py:141-157) which requires an on-disk asset,
  and `PipelineOrchestrator._mockup_source` (video/pin reuse).
- **What:** Any product older than ~3 days has NO local image left, so a
  marketing-refresh candidate returns "no existing asset found to
  re-promote" and is skipped — the refresh worker would rotate through the
  catalog posting almost nothing. Same starvation hits the SEO-refresh/video
  paths that reuse `_mockup_source` on resume.
- **Fix (pick one):**
  (a) EXEMPT one representative listing photo per published product from
  cleanup (e.g. keep `hero.png` for tasks with a listing_id), or
  (b) in `_pick_asset_path`, fall back to downloading the listing's primary
  image from Etsy (`EtsyImageService.get_listing_images` returns URLs) into a
  temp file. (a) is simpler and offline-safe; disk cost ≈ 1 image/product.
- **Verify:** Unit test: cleanup run leaves hero.png for published tasks;
  refresh cycle on an old product posts successfully.

### 3-3. Price bands silently cap market-grounded pricing — and the clamp is invisible
- **Where:** `PRODUCT_FORMATS` price_band (product_formats.py:72-89), market
  median grounding (pipeline_orchestrator.py:1409-1410).
- **What:** The real market p50 is used ONLY if it falls inside the band;
  otherwise it's silently discarded and the band midpoint/charm price wins.
  A 25-30 page planner (MAX_PDF_PAGES=30 now) competes at $9-18 on Etsy, but
  pdf band caps at $12; single_print caps at $8 while framed-style digital
  prints often sit $8-12. Underpricing is direct margin loss; there's also
  no signal recorded when p50 lands outside the band, so the bands never get
  recalibrated with data.
- **Fix:** (a) Record an analytics event `price_band_clamp` whenever
  market_price exists but falls outside the band (payload: format, p50,
  band) — after 2-4 weeks the events tell you exactly which bands are wrong.
  (b) Immediately: raise `pdf_planner_or_guide` to (5.00, 16.00) and
  `single_print` to (3.50, 10.00) — modest, evidence-aligned bumps; keep the
  rest until clamp data arrives.
- **Verify:** Unit test for the event; check /analytics/events for clamps
  after a week.

### 3-4. P&L fee estimate ignores Etsy Offsite Ads
- **Where:** `RevenueService.record_fee_estimate` (revenue_service.py:82-118).
- **What:** Offsite Ads takes 15% (shops <$10k/yr) when a sale is attributed
  — POD pricing left headroom for it (POD_ETSY_FEE_FRACTION=0.12) but the
  recorded per-sale fee estimate never includes it, so net P&L is
  systematically optimistic. Attribution isn't in the receipt payload the
  worker reads today, so a knob-based estimate is honest enough:
- **Fix:** `OFFSITE_ADS_ASSUMED_ATTRIBUTION_PCT` (default 0.10 — ~10% of
  sales attributed) × 15% added into the estimate, documented in the payload
  basis string. Optionally later: read
  receipt-level `is_gift`/ledger endpoints for real attribution.
- **Verify:** Unit test on the math; P&L tile shows slightly lower net.

### 3-5. Learning loop only reinforces THEMES — give it price/format profit signal
- **Where:** `_load_insights_block` (trend_research_agent.py:359-406),
  `BestProductsService.get_best_product_insights`.
- **What:** The insight block names top formats/keywords by revenue or view
  velocity, but never tells the concept LLM the PROFIT per format (a $12
  planner sale ≈ 4 coloring-page sales) or realized prices. The bias signal
  should be dollars, not counts.
- **Fix:** In the insights block, per top format include avg sale price and
  total net (revenue − fee_estimates): "pdf_planner_or_guide: $34.50 net
  from 3 sales (avg $11.50)". Data already exists in analytics events.
- **Verify:** Unit test rendering the block from fixture events.

### 3-6. Etsy shop plumbing that multiplies every listing's performance (manual, one-time)
- **What/How (Maj, ~30 min in Shop Manager + Railway):**
  1. Run `scripts/create_shop_sections.py`, then set `SHOP_SECTION_MAP` env —
     sectioned shops browse/convert better and it's already wired
     (pipeline_orchestrator.py:1353-1356).
  2. Set `SHOP_NAME` and optionally `WATERMARK_TEXT` (watermarks currently
     fall back to a default text — settings.py:288-290).
  3. Fill the shop About page + policies + banner (buyers check; conversion
     lever; nothing in code does this).
  4. Connect Pinterest OAuth (`/pinterest/oauth/...`) and set
     `PINTEREST_BOARD_MAP` per format — Pinterest is the strongest free
     traffic channel for printables and the entire integration is already
     built but dormant (pipeline skips when not connected,
     pipeline_orchestrator.py:1729-1732).
- **Verify:** Next published product lands in a section, and the pinterest
  stage reports ok instead of "not connected".

### 3-7. Run the still-pending STEP 105 shop-cleanup actions (manual)
- **What:** From the 105 audit, still queued: `python
  scripts/cleanup_low_score_listings.py --apply` (deactivates the 8 ≤3/10
  listings — dead weight suppressing shop-wide conversion) and `python
  scripts/backfill_occasion_metadata.py --apply` (so the seasonal lifecycle
  can manage pre-105 listings).
- **Verify:** Shop grid shows the low scorers gone;
  `SeasonalListingService.run` report counts >0 seasonal listings.

### 3-8. Keep the shop audit fresh — monthly re-audit feeds the cleanup tick
- **Where:** `LowScoreCleanupService._latest_report_path`
  (low_score_cleanup_service.py:33-36) reads the newest JSON in
  `instructions/audit_reports/` (currently only `2026-07-12.json`).
- **What:** The monthly dry-run cleanup tick will keep re-reporting July's
  data forever unless new reports land.
- **Fix:** Monthly (calendar reminder or a future scheduled agent): run
  `python scripts/audit_existing_listings.py`, commit the JSON into
  `instructions/audit_reports/YYYY-MM-DD.json`, review the Discord dry-run,
  then `cleanup_low_score_listings.py --apply` if it looks right.
- **Verify:** audit_reports/ gains a file each month.

### 3-9. Enforcement flip plan for the new gate (sequencing for Tier 1)
- **What:** After 1-1..1-7 ship: run 3-5 days in shadow
  (`PRODUCT_SCORE_ENFORCE=false`) with `CONCEPT_MODEL=anthropic/claude-sonnet-5`
  set. Check `GET /analytics/events?event_type=concept_scored`: the new rule
  should pass roughly 5-15% of scored concepts and the passes should be
  visibly the best ones. Then flip `PRODUCT_SCORE_ENFORCE=true`. If pass rate
  is 0% after 2 days, the floors are too tight → lower ONLY the det floor
  30→28 first (judges floor stays). If >30%, raise PRODUCT_MIN_SCORE 90→92.
- **Verify:** After the flip: 1-3 products/day created, and the 1-9 daily
  alert never fires two days straight.

### 3-10. Optional: engagement-triggered variants (revenue precursor)
- **Where:** winner-variant spawn fires only on a real sale
  (etsy_receipt_worker.py:596+).
- **What:** With few sales, the strongest available signal is view/favorite
  VELOCITY (already computed, performance_service.py:51-76). A listing doing
  10+ engagement/day for 3 straight days is a pre-sale winner.
- **Fix:** In the daily stats tick, if a listing's velocity ≥ threshold and
  no variant spawned from it this week, spawn ONE variant task (reuse
  `_maybe_spawn_winner_variant` with the same seasonal/trademark gates; cap
  1/day, respect spend caps). Env: `ENGAGEMENT_VARIANT_MIN_VELOCITY=10`,
  0 = off (default ON at 10 is reasonable).
- **Verify:** Unit test with fixture velocities.

---

## TIER 4 — Ops / security manual checklist (unchanged debts, consolidated)

These are carried from STEPS 102-105 and still open. None are code tasks.

1. **Rotate the Etsy + Tumblr OAuth secrets** (they existed in git history) —
   re-auth via `/etsy/oauth/login` and `/tumblr/oauth/...` after rotating
   keys in the respective dev consoles.
2. **Set `FACTORY_API_KEY`** in Railway — until set, every mutating endpoint
   is open to the internet (auth middleware is enforce-when-set,
   app/main.py:50-70).
3. **Configure `BACKUP_S3_*`** (Cloudflare R2 free tier works) — backups
   currently live on the same Railway volume they protect; the weekly nag
   alert is already firing.
4. **Confirm `AUTO_PUBLISH_LISTINGS=true`** — if it's false, everything ends
   as drafts and nothing can sell regardless of the gate (this was toggled
   during step 97 debugging).
5. **Set `CONCEPT_MODEL` and `SEO_MODEL`** to a strong model (see 1-6) — the
   two highest-leverage text calls in the system.
6. Merge `audit-step102-fixes` → `main` if Railway deploys from main (STEP
   105 noted ~30 commits on the branch; confirm what Railway tracks).

---

## TIER 5 — Small stuff / nits (log-everything section)

- 5-1. `BaseAgent._generate` logs the FULL prompt + output at INFO for every
  LLM call (base_agent.py:43-52) — Railway log volume + any secrets in
  context get persisted via LogService. Consider truncating payloads to 2k
  chars.
- 5-2. `ProductScoreService._judge` builds `ProductViabilityCriticAgent`
  per call — fine, but pass `min_score` explicitly if 1-1 changes floors so
  the judge's internal `passed` (unused) can't confuse future readers.
- 5-3. `snap_charm` can pick `base-1+0.49` BELOW the band floor when the
  band is narrow (candidates list includes base-1 before filtering; in_band
  filter protects it — OK, no bug; noted after checking).
- 5-4. `TrendResearchAgent.run()` loads `recent_product_titles(50)` AFTER
  research/intel calls — harmless, but moving it before saves two paid calls
  when the DB is unreachable (cycle would fail anyway).
- 5-5. `EtsyMarketService` counts come from `findAllListingsActive` `count`
  which Etsy caps/paginates — treat >10k values as "at least"; the
  competition banding already does effectively.
- 5-6. `_maybe_seasonal_lifecycle` runs 4 sub-ticks under one state save
  each — fine; if one raises, later ticks in the same pass are skipped until
  the next 300s poll (self-heals; no action).
- 5-7. `instructions/` root contains a stray `audit_reports/2026-07-12.json`
  ✓ correct location; also `full plan.txt`, `markdown.txt` etc. — consider a
  `docs/legacy/` sweep someday (cosmetic).
- 5-8. `concept_scored` events store the full judge reasons — good; add
  `min_score`+floors after 1-1 so distribution queries can segment by rule
  version (add a `rule_version: 2` field).
- 5-9. The dashboard has no concept-pipeline visibility at all (rooms show
  workers, not decisions). The 1-9 tile covers the essential part.
- 5-10. `venv/` AND `.venv/` both exist locally; only one is used. Cosmetic,
  disk only.

---

## SUGGESTED WORK ORDER (for working this file with Claude Code)

1. Tier 0 (diagnosis, no code) → paste findings into the session.
2. 1-1 + 1-2 + 1-3 together (one branch: "reachable strict gate + persistent
   search + best-of-pool") + their tests.
3. 1-4 + 1-5 (evidence quality) + tests.
4. 1-6, 1-7, 1-8, 1-9, 1-10 (config, memory, metering, alerting, near-miss).
5. Deploy → 3-9 shadow window → flip enforce.
6. Tier 2 items (2-1..2-7) as one quality pass.
7. Tier 3: 3-2 first, then 3-1 flips one at a time; 3-3..3-8 alongside.
8. Tier 4 checklist in Railway/Etsy consoles.
9. Tier 5 whenever convenient.

When all of Tier 0-3 is done the factory should: search persistently every
hour, build only concepts that clear an *achievable* excellent-on-every-axis
bar, price against real market data, promote continuously on two channels,
watch its own production rate, and report P&L that includes every real fee.
