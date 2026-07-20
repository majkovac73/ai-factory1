# AI-Factory — Audit & Optimization Plan (2026-07-20)

> Read-only, evidence-based audit of the live production system
> (`kind-liberation-production.up.railway.app`, Etsy shop **CardsForAllOcDesigns**,
> shop_id `58716525`). No product code was changed in this pass. All diagnostic
> scripts used are kept under `scripts/audit/` and were run **inside the Railway
> container** (`railway ssh`, never `railway run`) against the real `/data/app.db`
> and the live Etsy/Pinterest APIs.

---

## 0. Executive summary

**Is the system, today, capable of autonomously making a sale with zero human
intervention? Verdict: MECHANICALLY YES, COMMERCIALLY NO — it has never made
one, and nothing in the current setup is likely to change that.**

Chain of evidence I actually walked:

1. The pipeline **works end-to-end and publishes complete, sellable listings.**
   I read back 6 live listings via the authoritative Etsy per-listing endpoints
   (`GET /listings/{id}/images`, `.../files`): each is `state=active`, has **2–4
   real photos** (1024×1024), **1–5 digital files** (`design.pdf`, …), a real
   price (€3.50–€19.99, p50 €5.99), taxonomy that **varies by format** (339/354/
   2078/1326/1280), `who_made=i_did`. (An earlier reading of "0 images" from the
   bulk `listings/active?includes=Images` endpoint was a **diagnostic artifact** —
   that endpoint doesn't populate the images array; the per-listing endpoint
   proves images are attached. Retracted.)
2. So a digital sale would auto-deliver with no human step. **But zero sales have
   occurred.** Etsy's own shop object: `transaction_sold_count=0`, `review_count=0`,
   `is_vacation=false`, 45 active listings. The DB has **no sale/receipt/order
   event of any kind** (`analytics_events` has only `listing_stats`,
   `concept_scored`, `marketing_post_*`, `task_completed`); `fulfillment_records=0`.
3. **Why: no traffic + unproven appeal.** The system's own tracking shows **~7
   total listing views across 43 tracked listings in 13 days**. The one
   buyer-intent marketing channel (Pinterest) is **90% failing** — `marketing_posts`:
   Pinterest 4 success / 41 failed, every failure `403 code 29 "Apps with Trial
   access may not create Pins in production."` Only Tumblr posts land (48 success),
   and Tumblr drives little Etsy purchase traffic. There is no paid traffic, no
   re-promotion (`MARKETING_REFRESH_ENABLED=false`), and the shop is brand-new
   with zero reviews/authority.
4. **Product appeal is mediocre by the system's own judges.** All **81** scored
   concepts landed **55–71 (mean 63), and 0 passed** the quality floors. Cause:
   `CONCEPT_MODEL=None` and `DEFAULT_MODEL=openai/gpt-4o-mini`, so the concept
   generator *and* both "independent" score judges are all the same weak model.
   The gate runs in **shadow mode** (`PRODUCT_SCORE_ENFORCE=false`), so these
   ~63/100 products publish anyway; and flipping enforcement on *as configured*
   would reject everything (0/81 pass) → zero production.

Net: this is not a broken factory — it is a **working factory with no
distribution and unproven products**, spending ~$0.29/day to manufacture
listings that no one sees and that aren't compelling enough to sell without
traffic.

| Tier | Count | One-line theme |
|------|-------|----------------|
| **P0** | 3 | No buyer traffic (Pinterest blocked), mediocre product appeal on the wrong model tier, and the quality gate can't be turned on without killing production |
| **P1** | 8 | No profit/cost visibility, wasted Pinterest image spend, no re-promotion, weak SEO (truncated/under-filled tags), low-res images, blind learning loop |
| **P2** | 5 | No rate-limit backoff, backups not off-box (SPOF), logging/audit trail gaps, no FAILED-task surfacing |
| **P3** | 4 | Shop-level branding, product-mix/margin review, Pinterest App-ID hygiene, trademark/derivative flagging |

---

## 1. Prioritized action list

### [P0] #1 — The only buyer-intent marketing channel (Pinterest) is 90% failing; the shop has effectively no traffic
**Where:** `app/marketing/pinterest_channel.py`, `app/services/pipeline_orchestrator.py` stage 8 (`_stage_pinterest`); Pinterest app `1589935` ("ai-factory2", Trial access); `marketing_posts` table.
**Evidence:** `marketing_posts` channel×status = `pinterest success 4 / failed 41`; every recent failure body: `403: {"code":29,"message":"Apps with Trial access may not create Pins in production"}` (2026-07-20 17:16–18:12). Etsy shop `transaction_sold_count=0`, `review_count=0`. System `listing_stats`: **7 total views across 43 listings**. Tumblr works (48 success) but is low buyer-intent. `MARKETING_REFRESH_ENABLED=false` (verified live) → no re-promotion.
**Why it matters (money impact):** Zero qualified traffic = zero sales, full stop. Every other investment (product gen, SEO, images) is wasted until buyers arrive. Pinterest is the highest-intent free channel for printables/wall-art; with it blocked, the shop is invisible. This is the single largest revenue blocker.
**Fix:** (a) Land Pinterest **Standard access** — this is already in flight and is blocked on a *config* problem, not code: `PINTEREST_APP_SECRET` in Railway is a 101-char value (an access token, not the ~64-char app secret) for App `1589935`, causing `401` on the OAuth token exchange. Set the real App-secret for `1589935`, re-verify via `python -c "from config import settings as s; print(len(s.PINTEREST_APP_SECRET))"`, complete the OAuth demo, submit for Standard. (b) Until then, **stop paying to generate Pinterest images that can't post** (see #6) and lean on Tumblr + at least one additional channel. (c) Durable fix: add a `PINTEREST_CAN_PUBLISH` capability probe so the pipeline skips pin work while Trial-blocked instead of failing 41×.
**Verification required after fix:** `marketing_posts` shows new `pinterest success` rows with real `external_id` pin IDs (not sandbox), and Etsy shop `views`/`transaction_sold_count` begin rising over a 7-day window.

### [P0] #2 — Concept generation and the quality judges all run on gpt-4o-mini; products self-score ~63/100 and none are genuinely compelling
**Where:** `config.settings` (`CONCEPT_MODEL=None`, `DEFAULT_MODEL=openai/gpt-4o-mini`); `app/services/product_score_service.py`; `app/agents/trend_research_agent.py`.
**Evidence:** Live config: `CONCEPT_MODEL = None`, `DEFAULT_MODEL = openai/gpt-4o-mini`. `ProductScoreService.__init__` falls back `concept_model = concept_model or CONCEPT_MODEL or DEFAULT_MODEL`, so **both judges are gpt-4o-mini** (the "judges are the SAME model — judgment is NOT independent" warning is active). `concept_scored` distribution (n=81): min 55, max 71, mean 63, **0 ≥ 90, 0 ≥ 75**; total = deterministic(0-40) + 6×harsher_judge(0-60), so the LLM judges are scoring ~5-6/10.
**Why it matters (money impact):** "What to build" is the highest-leverage decision in the whole system — a great concept on a saturated market still beats a bland one. Running it (and the quality bar) on the cheapest model caps the ceiling of everything downstream: the shop fills with "bland but not broken" products a buyer scrolls past in favor of an established seller. Cheap here is false economy — the model cost delta ($/concept) is trivial versus one sale.
**Fix:** Set `CONCEPT_MODEL` to a strong model (e.g. `anthropic/claude-sonnet-5`) in Railway. This simultaneously (a) raises concept quality and (b) makes the two score judges genuinely independent (concept_model vs default_model), which is required for the floors gate to mean anything. Keep `DEFAULT_MODEL` for cheap downstream work. Re-run several autonomy cycles and inspect the new `concept_scored` mean.
**Verification required after fix:** New `concept_scored` events show the two judges on *different* models with a materially higher mean (target median ≥ 80 and a non-zero pass rate), and the independence warning stops firing.

### [P0] #3 — The quality gate cannot be enabled as configured: flipping `PRODUCT_SCORE_ENFORCE=true` today would reject 100% of concepts → zero production
**Where:** `app/services/product_score_service.py` (`score()` floors), `app/agents/trend_research_agent.py` (`_propose_from_insight` enforce path), settings `PRODUCT_MIN_SCORE=90`, `PRODUCT_JUDGE_FLOOR=9`, `PRODUCT_DET_FLOOR=30`.
**Evidence:** Floors require `total≥90 AND harsher_judge≥9 AND det_total≥30 AND no axis at floor`. Live data: **0 of 81** concepts meet these (max total 71, judges ~5-6). In enforce mode, `_propose_from_insight` returns `None` when nothing passes; a cycle that finds no passer creates no task → zero products/day. This is a latent trap: the plan of record (per prior notes) is to flip `PRODUCT_SCORE_ENFORCE` after a few shadow days, which would silently halt the factory.
**Why it matters (money impact):** Enabling the gate is the intended path to stop wasting ~$0.03×N/day on bland products; done in the wrong order it instead spends $0 by producing nothing, and the "zero production" alert would fire daily. It also masks #2 — the real problem isn't the threshold, it's that the products genuinely aren't good.
**Fix:** Strict ordering: **do #2 first** (strong `CONCEPT_MODEL`), keep shadow mode until `concept_scored` shows a healthy pass rate (e.g. ≥30% of scored concepts pass the floors across a day), *then* set `PRODUCT_SCORE_ENFORCE=true`. Add a guardrail: if enforce is on and a cycle produces 0 passers for N consecutive cycles, auto-alert (the `concept_near_miss` machinery in `trend_research_agent._persist_best_failed` already exists — wire it to the zero-production alert).
**Verification required after fix:** With enforce on, Railway logs/`task_completed` show sustained non-zero daily production AND `concept_scored.passed=true` rate matches the pre-flip shadow rate (no cliff).

### [P1] #4 — No per-product cost or profit ledger; unit economics are invisible
**Where:** `analytics_events` (no cost event type); `/data/autonomy_state_*.json`; `app/services/autonomy_service.py`; `app/services/revenue_service.py`.
**Evidence:** `analytics_events` distinct `event_type` = `{listing_stats, marketing_post_failed, marketing_post_success, concept_scored, task_completed}` — **no cost/spend/image_gen event**. Spend is only aggregated per day in tiny JSONs: `autonomy_state_2026-07-20.json = {"tasks_created":10,"spend_usd":0.298}` (≈$0.03/product). There is no per-listing or per-format profit anywhere; `revenue_service.profit_by_format()` exists but has $0 revenue to work with and no cost side.
**Why it matters (money impact):** You cannot optimize what you cannot measure. With no cost-per-product-per-format and no profit view, the learning loop and the human operator are both blind to which formats/themes actually pay after Etsy fees (listing $0.20, 6.5% transaction, ~4% payment, +offsite ads). At scale this is the difference between doubling down on winners and uniformly funding losers.
**Fix:** Emit a `cost_incurred` analytics event at the provider choke points (image gen in `openrouter_image_provider`, each vision-QA call) with `entity_type='task'`, `entity_id=task_id`, `value=usd`, `payload={provider,model,use_case}`. Add `RevenueService.pnl_by_listing()` joining `cost_incurred` (spend) + Etsy `transaction`/receipt revenue − Etsy fees. Surface it on `/dashboard`.
**Verification required after fix:** A DB query sums `cost_incurred` per `task_id` and reconciles (±10%) with the daily `autonomy_state` totals; dashboard shows net P&L per listing.

### [P1] #5 — Pinterest listing images are generated (paid) then thrown away because Trial access can't post them
**Where:** `app/services/pipeline_orchestrator.py` `_stage_pinterest`; `app/services/pinterest_image_service.py`; `image_assets`.
**Evidence:** `image_assets` use_case=`pinterest` provider=`openrouter` model=`bytedance-seed/seedream-4.5` count **24**, yet `marketing_posts` shows 41 Pinterest post failures (Trial 403). So a Seedream image is generated per product then the post fails.
**Why it matters (money impact):** ~24 Seedream calls (~$0.03 each ≈ $0.72 over 13 days, scaling with volume) spent on images that never reach Pinterest. Small today, but it's pure loss and grows linearly with production while Pinterest stays Trial-blocked.
**Fix:** Gate `_stage_pinterest`'s image generation on an actual publish-capability check (extend `pinterest_oauth.is_connected()` to a `can_publish()` that is False under Trial), and short-circuit before the paid image call. Re-enable automatically once Standard access lands (#1).
**Verification required after fix:** Zero new `image_assets` rows with `use_case='pinterest'` while Trial-blocked; they resume only after `pinterest success` posts appear.

### [P1] #6 — No re-promotion of listings; each product is marketed once and then goes silent
**Where:** `MARKETING_REFRESH_ENABLED=false` (live); `app/workers/marketing_refresh_worker.py`.
**Evidence:** Live config `MARKETING_REFRESH_ENABLED=false`. Marketing fires once in the main pipeline (stage 8) at creation; the refresh worker (periodic re-posting of existing catalog) is disabled. 45 listings, ~7 views total.
**Why it matters (money impact):** New Etsy listings need sustained, repeated social touches to accumulate the click/favorite/sale signals that improve search rank. A single post at t=0 (to a low-reach channel, with Pinterest failing) generates one impression spike and nothing after — the catalog's 45 products get no ongoing promotion, so they never build momentum.
**Fix:** Once Pinterest Standard is live (#1), enable `MARKETING_REFRESH_ENABLED=true` with a sane cadence and confirm the refresh worker rotates through the real published catalog (it already selects readback-verified listings). Until Pinterest is fixed, refreshing only to Tumblr has limited value — sequence after #1.
**Verification required after fix:** `marketing_posts` shows repeat posts per listing over time across ≥2 channels, and per-listing `listing_stats.views` climb post-refresh.

### [P1] #7 — SEO tags are truncated mid-word at Etsy's 20-char limit, and 40% of listings under-use tags
**Where:** `app/agents/etsy/seo_generator.py`; live Etsy listing tags.
**Evidence:** Across the 45 live listings, **220 tags are ≥20 chars**, many clearly truncated mid-word: `"classroom organizati"` (from "classroom organization"), `"inspiring quotes tag"`, `"printable teacher ta"`, `"classroom decoration"`. Tag-count distribution: only **27/45 listings use all 13 tags**; 18 use 3–8.
**Why it matters (money impact):** Etsy search matches whole tag phrases. A tag truncated to `"classroom organizati"` matches *no* real buyer query — it's a wasted slot. Under-filling tags (3 of 13) leaves free discoverability on the table. Both directly reduce impressions, the top of the funnel that is already near-zero.
**Fix:** In `seo_generator`, enforce the 20-char limit at generation time — reject/rewrite any candidate tag >20 chars to a shorter *whole-word* phrase rather than letting Etsy hard-truncate, and require exactly 13 valid tags per listing (backfill from the concept's `seo_context`/rising queries). Add a post-generation validator that asserts `len(tag)<=20 and tag==tag.strip()` for all 13.
**Verification required after fix:** Re-pull live tags; 0 truncated tags, all listings carry 13 tags. Consider a one-off backfill script to fix the existing 45.

### [P1] #8 — Listing photos are 1024×1024 square — below Etsy's recommended resolution and aspect ratio
**Where:** image generation/compositing (`openrouter_image_provider`, `MockupService`/scene composites); verified live dims.
**Evidence:** `GET /listings/{id}/images` returns `full_width×full_height = 1024×1024` for sampled listings. Etsy recommends ≥2000px on the shortest side and a landscape ratio (4:3/5:4) for the primary photo to avoid cropping in the grid.
**Why it matters (money impact):** The thumbnail is the single biggest driver of click-through from search. A 1024px square image renders softer than competitors' 2000px+ landscape shots and can be awkwardly cropped in Etsy's grid, lowering CTR — again at the scarce top of the funnel.
**Fix:** Generate/composite listing images at ≥2000px and produce the hero as a landscape (e.g. 2000×1600) mockup; keep square only where the format demands it. Adjust the Seedream request size and the PIL scene-composite canvas.
**Verification required after fix:** New listings' `full_width ≥ 2000` and hero aspect is landscape.

### [P1] #9 — The learning loop is blind: it keys off view-velocity, and there are ~0 views and 0 sales to learn from
**Where:** `app/agents/trend_research_agent.py::_load_insights_block`; `app/services/best_products_service.py`, `revenue_service.py`.
**Evidence:** `_load_insights_block` biases concepts toward "best" formats by view velocity and toward recorded revenue; live revenue = $0 and total views ≈ 7. So the insight block is either empty or driven by noise (7 views across 43 listings). No sales feed back into selection because there are none.
**Why it matters (money impact):** Every autonomous cycle is effectively a cold guess — the system can't yet get smarter over time, so it will keep producing the same ~63/100 mix. The feedback flywheel only spins once #1/#2 produce real traffic and sales; until then the loop adds no value and can even chase 1-view noise.
**Fix:** Until real signal exists, weight the insight block toward *external* demand (the real Google-Trends rising queries already fetched) rather than internal near-zero view velocity, and suppress the "biased toward proven themes" language when total views < a floor. Re-enable performance-weighting once weekly sales ≥ a threshold.
**Verification required after fix:** With traffic flowing, `concept_scored`/task themes shift measurably toward formats/keywords with the best real per-listing conversion.

### [P1] #10 — Real Google-Trends grounding may be thin in practice (demand axis frequently scores low)
**Where:** `app/services/trend_data_service.py`, `app/agents/trend_research_agent.py` (aborts on `TrendDataFetchError` — good), `product_score_service._demand`.
**Evidence:** `TrendResearchAgent.run()` correctly **hard-aborts** the cycle on `TrendDataFetchError` (no LLM-imagination fallback — the previously-fixed bug class is honored here). However, the deterministic `demand` subscore uses `trend_data.rising_queries`/`interest_trend`, and the low `concept_scored` totals (mean 63, det floor often not met) are consistent with `demand` frequently returning the "no trend data / no matching keyword" default (4/10). The DB `logs` table (INFO/WARNING only) does not persist trend-fetch outcomes, so live pytrends success/coverage is **UNVERIFIED from the DB** — only stdout would show it.
**Why it matters (money impact):** If the trend signal is usually empty, the "grounded in real demand" premise is weaker than intended and concepts drift toward generic evergreen guesses — which is exactly the mediocrity in #2. It also means the demand axis rarely contributes to passing the gate.
**Fix:** Add a `trend_signal` analytics event per cycle (`{keywords_fetched, rising_query_count, matched}`) so coverage is measurable from the DB, not just stdout. If coverage is low, widen the seed-keyword set or the matching in `_demand`. (No fallback-to-imagination — keep the hard abort.)
**Verification required after fix:** `trend_signal` events show real rising-query counts >0 for most cycles; `product_score_service._demand` reasons cite matched queries, not "no trend data," on the majority of concepts.

### [P1] #11 — No FAILED/BLOCKED tasks ever recorded across 147 tasks — failures may not be surfaced
**Where:** `tasks` table; `app/services/pipeline_orchestrator.py::_block_task`.
**Evidence:** `tasks.status` distribution = `{DONE: 147}` (0 FAILED, 0 BLOCKED, 0 in-progress); `retry_count=0` on all. Over 13 days with real image/API/LLM calls and 41 Pinterest failures, zero task-level failures is improbable unless blocks/failures aren't persisted as task states (marketing failures are recorded on `marketing_posts`, not the task).
**Why it matters (money impact):** If a task that fails a QA/readback gate is cleaned up but not left in a visible FAILED/BLOCKED state, you lose the signal for *why* products aren't shipping (and the near-miss/approval queue can't surface them). It also hides silent-failure regressions.
**Fix:** Confirm `_block_task` sets a persistent `BLOCKED`/`FAILED` status (not deletion) and that the dashboard/alerts count them. If blocks currently mark tasks DONE or remove them, change to a distinct terminal state and add a daily "N tasks blocked, top reasons" alert.
**Verification required after fix:** Deliberately fail a gate in a test cycle; the task shows a `BLOCKED`/`FAILED` row with a reason, and it appears in the daily alert.

### [P2] #12 — No rate-limit/backoff handling for any external API; live Etsy calls already return 429
**Where:** `app/services/etsy_client.py`, `etsy_image_service.py`, `printify_client.py`, `app/marketing/*`.
**Evidence:** Grep for `429|backoff|Retry-After` across the Etsy/Printify/marketing clients returns **only** one publish-retry `asyncio.sleep` — no 429 handling. My own read-only audit script hit `ERR429` on 2 of 6 rapid sequential Etsy calls. The hourly `listing_stats` poll iterates all 45+ listings.
**Why it matters (money impact):** Under normal growth (more listings × hourly stats + marketing bursts) the system will hit Etsy's rate limits with no backoff, causing failed reads/writes and — worse — repeated hammering that Etsy can interpret as abuse and throttle or suspend the app. An Etsy suspension is an existential, revenue-ending event.
**Fix:** Add a shared retry/backoff wrapper (respect `Retry-After`, exponential backoff, cap) around all external HTTP calls in the Etsy/Printify/Pinterest/Tumblr clients; add small inter-call delays in the listing-stats poll loop.
**Verification required after fix:** A burst test against Etsy shows automatic backoff on 429 with eventual success and no unhandled 429 in logs.

### [P2] #13 — Database backups exist but stay on the same Railway volume (no off-box copy) — single point of failure
**Where:** `app/services/backup_service.py`; triggered from `app/workers/etsy_receipt_worker.py`.
**Evidence:** `BACKUP_ENABLED=true` and `create_backup()` is called from the receipt worker, but live `BACKUP_S3_BUCKET` is **unset**, so `_upload_offsite` is skipped and backups are kept as local zips under `/data` (last-7). The primary DB is a single 48 MB `/data/app.db` on the same volume.
**Why it matters (money impact):** If the Railway volume is lost/corrupted, the DB **and** its backups vanish together — losing the entire catalog↔listing mapping, token store, and history. Recreating that (and reconnecting OAuth) is hours of work and risks orphaned/duplicate listings. This is the classic SPOF.
**Fix:** Configure `BACKUP_S3_*` (any S3-compatible bucket — Railway, Backblaze B2, R2) so `_upload_offsite` runs; verify `boto3` is installed. This is a config task, not code. Relevant to the planned Oracle Cloud VM move: get off-box backups working *before* migrating.
**Verification required after fix:** A backup object appears in the external bucket dated today; restore-from-bucket rehearsed once.

### [P2] #14 — The persisted `logs` table captures no errors and no agent activity — spend/failures aren't traceable from the DB
**Where:** `logs` table; `app/services/log_service.py`; logger usage across agents/workers.
**Evidence:** `logs` level distribution = `{INFO: 1843, WARNING: 9}`, **zero ERROR/CRITICAL**. The only recurring persisted messages are "Etsy access token refreshed" (hourly) and "LLM generation completed." Grep of the table for `trend|fallback|429|cost|seedream` returns nothing — the rich agent logs (TrendResearchAgent, ProductScoreService, pin failures) go to stdout only and are lost when the container recycles.
**Why it matters (money impact):** The audit rule "every dollar traceable from logs alone" fails: you cannot reconstruct what was spent or why a product failed from the DB. Debugging a production incident depends on catching ephemeral stdout in real time. It also means the "no ERROR ever" picture is false comfort.
**Fix:** Route WARNING/ERROR (and cost/trend milestone events) through `LogService` into the `logs` table (or ship stdout to a persistent log store). At minimum, persist errors and the per-cycle cost/trend summary rows.
**Verification required after fix:** After a forced error, an ERROR row with source + payload appears in `logs`; a day's spend is reconstructable from DB rows.

### [P2] #15 — Execution audit tables (`agent_executions`, `task_steps`) exist but are empty — no per-step provenance
**Where:** `agent_executions` (0 rows), `task_steps` (0 rows); `app/ai/router.py`.
**Evidence:** Both tables have 0 rows despite 147 completed tasks. So there is no persisted record of which agent/model handled which step, or the router's decisions — routing is not auditable.
**Why it matters (money impact):** Without per-step provenance you can't attribute cost or quality to a specific agent/model, can't detect a mis-routed step (e.g. an expensive model doing a trivial job), and can't audit AI-router false positives/negatives the audit asks about. It's the data substrate for both #4 (cost) and future optimization.
**Fix:** Populate `agent_executions`/`task_steps` (or drop them if truly superseded). Minimum: record `{task_id, step, agent, model, tokens/cost, status}` per pipeline stage. This also gives the router an auditable decision log.
**Verification required after fix:** New tasks produce `task_steps` rows for each stage with model + cost; a query shows per-stage model usage.

### [P3] #16 — Shop-level trust signals are unfinished (no announcement/About; 0 reviews) — buyers judge a bare shop as risky
**Where:** Etsy shop `58716525` (CardsForAllOcDesigns); shop policies/About (not managed by the pipeline).
**Evidence:** Etsy shop object: `announcement=None`, `review_count=0`, `review_average=0`. The pipeline manages listings but nothing sets shop announcement, About/Story, or a banner.
**Why it matters (money impact):** Even with traffic, a first-time buyer landing on a shop with no announcement, no About, and 0 reviews hesitates — trust is the conversion gate for an unknown seller. Cheap to fix, compounding benefit.
**Fix (flag for Maj — largely manual):** Write a shop announcement, About/Story, clear digital-download policy, and a simple banner. Consider seeding initial credibility (a few honest early sales/reviews via legitimate promotion). Not a code task; flag for Maj.
**Verification required after fix:** Etsy shop object shows a non-null `announcement` and completed policies/About.

### [P3] #17 — Product mix is heavily PDF planners + coloring pages; validate that demand *and* margin actually exist there
**Where:** `tasks.type` distribution; `app/core/product_formats.py` price bands; `EtsyMarketService` competition/price data.
**Evidence:** `tasks.type`: `pdf_planner_or_guide 56, coloring_page 50, single_print 20, sticker_sheet 6, seamless_pattern 3, phone_wallpaper 3, greeting_card 3`. Live price p50 €5.99; coloring pages priced €3.50 (band floor). The system's own `EtsyMarketService` pulls competition_count + price_p50 per concept, but external competitor benchmarking could not be pulled here (Etsy returns 403 to WebFetch — **UNVERIFIED externally; needs manual competitor lookup**).
**Why it matters (money impact):** Coloring pages at €3.50 in a saturated category earn almost nothing per sale after fees and are the hardest to rank as a new shop; planners at €10–15 have far better unit economics. A mix skewed toward €3.50 low-margin items in crowded niches caps revenue even if traffic arrives.
**Fix:** Use the (now cost-aware, #4) profit-by-format signal plus the existing market competition_count to bias the concept generator toward higher-margin, less-saturated formats; raise the coloring-page floor or de-prioritize it. Maj to spot-check 5 real competitor listings per top format for price/review reality.
**Verification required after fix:** New product mix shifts toward formats with the best (real) profit-per-sale; competitor spot-check documented.

### [P3] #18 — Pinterest App-ID hygiene: production uses `1589935`; the `1587865` in prior notes is a different app
**Where:** live `PINTEREST_APP_ID=1589935`; instructions referenced `1587865` vs `1589935`.
**Evidence:** Live prod config `PINTEREST_APP_ID = 1589935`; Pinterest portal shows app **"ai-factory2", App ID 1589935, Trial access active**. The `1587865` referenced in `instructions_pinterest_app_review_resubmission.md` is a separate/older app. `PINTEREST_SANDBOX=true` is still set in prod (the OAuth flow now forces production for token exchange, so this no longer blocks OAuth, but it's stale).
**Why it matters (money impact):** Chasing Standard access or debugging OAuth against the wrong App ID wastes cycles and can leave the wrong app configured — indirectly delaying the #1 traffic fix. Stale `PINTEREST_SANDBOX=true` is a latent footgun for the non-OAuth pin path.
**Fix:** Standardize on `1589935` everywhere (app secret, redirect URI, review submission). Unset `PINTEREST_SANDBOX` in Railway now that OAuth exchange is production-pinned. Update the instructions docs to drop `1587865`.
**Verification required after fix:** All Pinterest config references `1589935`; `PINTEREST_SANDBOX` unset; a real production pin posts after Standard access.

### [P3] #19 — Trademark screening is present and wired (good) — but generated-design derivative risk still needs a human eye
**Where:** `app/core/trademark_screen.py` (used in `trend_research_agent._validate_product` AND `product_score_service._hard_gate`).
**Evidence:** `_tm_screen(name, description)` is called as a hard gate at concept validation and re-checked in the score service's `_hard_gate`; a hit rejects the concept with IP-risk feedback. Verified present in both paths — **no issue found in the text-level screen.**
**Why it matters (money impact):** Text screening catches branded *names*, but an AI-generated *image* can still be derivative of protected IP (a character silhouette, a known art style) without a trademarked word in the concept — an Etsy takedown/suspension risk. The screen reduces but doesn't eliminate this.
**Fix (flag for Maj):** Keep the text screen; add a lightweight visual-IP sanity note to the design prompt ("no recognizable characters, logos, or copyrighted artwork"), and have Maj periodically spot-check generated art. Not a legal opinion — flag for confirmation.
**Verification required after fix:** Spot-check of a sample of live listing images finds no derivative IP; design prompt includes the no-IP instruction.

### [P2] #20 — Idempotency on publish is handled correctly (verified — no issue), retained here for coverage
**Where:** `app/services/pipeline_orchestrator.py` (`record_created_listing` at line ~1580, "5-1" comment) and resume logic.
**Evidence:** The orchestrator persists the verified `listing_id` to the task **before** the final completed stamp specifically so a crash/resume can't create a duplicate listing; readback verification gates images/files/publish-state before success. `tasks.retry_count=0` and no duplicate listing IDs observed across the 45 live listings. **No issue found — verified via code + live listing uniqueness.**
**Why it matters (money impact):** Duplicate listings would waste $0.20 Etsy fees each and split search signal; duplicate publishes could double-charge. The existing guard prevents this.
**Fix:** None required. Keep the guard; ensure the same pattern is applied if a POD path is ever re-enabled (`pod_products=0` today).
**Verification required after fix:** N/A (already correct) — re-verify listing-ID uniqueness after any resume-path change.

---

## 2. Full-system coverage map (proof nothing was skipped)

Each checklist area maps to at least one numbered item above (silence = checked, not forgotten):

- **A. Core pipeline & orchestration** → #11 (task state machine: all DONE, no FAILED surfaced), #15 (empty `agent_executions`/`task_steps`, router not auditable), #20 (idempotency verified OK). Planner→Executor→QA readback gates verified present in `pipeline_orchestrator._stage_attach_publish` (images/files/publish-state readback with `_block_task`).
- **B. Trend research & ideation** → #10 (pytrends: hard-abort on failure verified = **no silent fallback**, but live coverage unverified from DB), #2/#9 (LLM-invented concepts grounded weakly on gpt-4o-mini), dedup verified in `trend_research_agent._dedup_error` (difflib >0.75, same-format).
- **C. Product generation & quality gate** → #2, #3 (ProductScoreService wired into the live path via `TrendResearchAgent`; floors reachable in theory, 0/81 in practice), #17 (image cost per product ≈$0.03 vs €3.50–€19.99 price — unit economics *positive per unit* once sold). Digital files: verified attached (1–5 `design.pdf`/etc. per listing) — deliverable presence confirmed, **content-quality of PDFs not opened (partially verified)**. POD: `pod_products=0`, paused — not a current risk.
- **D. Etsy integration** → #7 (SEO tags truncated/under-filled), #8 (image resolution), plus verified-OK: OAuth header format is `keystring:sharedsecret` (a bare keystring 403s — confirmed live), `taxonomy_id` varies by format (339/354/2078/1326/1280), `who_made=i_did`, `when_made`/`taxonomy` mismatch-gated in `_create_listing`, prices formula/band-based (€3.50–€19.99). Photos present (earlier "0 images" retracted).
- **E. Marketing** → #1 (Pinterest Trial-blocked, the core traffic gap), #5 (wasted pin images), #6 (`MARKETING_REFRESH_ENABLED=false` = no re-promotion — a real loss, not just a forgotten flag), #18 (App-ID hygiene). Tumblr verified working (48 success) and correctly hyperlinks back to the Etsy listing (`tumblr_channel` NPF link). No analytics tie marketing → Etsy views/sales → folded into #4/#9. Missing near-zero-cost channels (Pinterest idea pins, etc.) blocked by #1.
- **F. Revenue, unit economics & analytics** → #4 (no cost ledger / no profit-per-listing — major blind spot), #9 (no winner-detection feedback because no sales), single biggest silent waste = **the entire pipeline spend on products no one sees** (~$0.29/day × 13d ≈ $3.7 compute + 45×$0.20 = $9 Etsy listing fees, $0 revenue) plus the smaller Pinterest-image waste (#5).
- **G. Reliability, security & operational risk** → #12 (no rate-limit backoff; live 429s), #13 (backups not off-box = SPOF), #14 (logs table captures no errors), #20 (idempotency OK). Secrets: `FACTORY_API_KEY` now SET (mutating routes protected); Discord alert webhook set; OAuth tokens live in DB (`etsy_tokens`/`pinterest_tokens`/`tumblr_tokens` = 1 each) — prior git-history token exposure was noted previously and should stay rotated (**flag for Maj to confirm rotation**).
- **H. Business-model level** → #17 (product mix + margin; external competitor pricing **UNVERIFIED — needs manual Etsy lookup**, WebFetch 403s), #16 (shop trust/branding), #19 (trademark screen present; visual-IP risk flagged), #13 (SPOF). Category demand grounded in `EtsyMarketService` competition_count + Google-Trends (subject to #10's coverage caveat).

---

## 3. "Ready to run on its own" gate (go / no-go)

The system already runs unattended and publishes valid listings, but it is **NOT yet ready to be trusted to make money unattended.** Minimum P0/P1 items that must be true before leaving it fully alone:

- [ ] **#1** Pinterest posting works in production (Standard access granted; real `pinterest success` rows) — i.e., there is a real, functioning buyer-traffic channel. *(Currently blocked only on setting the correct App-secret for `1589935`.)*
- [ ] **#2** `CONCEPT_MODEL` set to a strong model; new `concept_scored` median ≥ 80 with a non-zero pass rate and two genuinely independent judges.
- [ ] **#3** Quality gate enabled **in the correct order** (only after #2), with a zero-production guardrail wired to alerts — so enforcement improves quality without silently halting the factory.
- [ ] **#5/#6** Pinterest image spend gated on publish-capability, and re-promotion enabled once Pinterest works — so marketing spend isn't wasted and listings get sustained promotion.
- [ ] **#12** Rate-limit backoff on all external APIs — so unattended running cannot hammer Etsy into a suspension.
- [ ] **#13** Off-box backups configured and verified — so an unattended volume loss isn't catastrophic.
- [ ] **#4** A per-product cost→revenue ledger exists — so "running on its own" is observable and a runaway-spend or all-loss trend is detectable without watching logs live.

Until #1 and #2 are true, autonomous operation only manufactures unsold inventory. They are the two that convert this from "a working factory" into "a factory that can actually make a sale."

---

*Diagnostic scripts (kept, reusable): `scripts/audit/db_overview.py`, `db_business.py`,
`db_listings_scores.py`, `live_state.py`, `logs_and_etsy.py`, `etsy_readback.py`,
`etsy_all.py`, `etsy_images_verify.py`, `ops_check.py`. Run inside the container via
`railway ssh "cd /app && echo <base64-of-script> | base64 -d | python3"`.*
