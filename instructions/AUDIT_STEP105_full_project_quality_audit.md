# STEP 105 — Full-Project Quality Audit + the ≥95 Product Score Gate (2026-07-12)

Fourth full-project audit, done after ALL of STEP 104 shipped. Covers technical,
business, product-generation and marketing subsystems end-to-end. Trigger: Maj
wants the factory to ONLY build products genuinely worth buying — a hard
scoring gate at **95/100** — plus a full sweep for anything else broken or
improvable. Pinterest is deliberately NOT audited (known incomplete, Maj's
manual action).

Hard evidence anchoring this audit: `audit_report.json` (a live run of
`scripts/audit_existing_listings.py` against the real shop, currently sitting
untracked in the repo root) shows **28 live listings, only 6 pass the critic,
average viability score 4.1/10** — including out-of-season Easter, Mother's
Day, Cinco de Mayo and Thanksgiving coloring pages scoring 1-2. The factory's
plumbing now works; the PRODUCTS are the bottleneck. That is why Tier 1 of this
audit is the concept-quality gate, not more plumbing.

Ordered most → least important. Each finding: WHAT, WHERE, WHY it costs money,
HOW to fix, and how to VERIFY. Work them top-down as separate steps with Claude
Code.

Production reality assumed: AUTONOMY_ENABLED=true, AUTO_PUBLISH_LISTINGS=true,
MAX_TASKS_PER_DAY=10, MAX_DAILY_SPEND_USD=5, Railway + persistent volume,
Printify shop 28166438, Tumblr connected, Pinterest NOT connected,
MARKETING_REFRESH_ENABLED=false, POD paused, WALL_ART_SET_ENABLED=false,
LISTING_VIDEO_ENABLED=false. Branch `audit-step102-fixes` holds ~30 commits not
yet on `main`.

---

## TIER 1 — Product quality: build ONLY products worth buying

### 1-1. The new ≥95 product score gate (Maj's core request — replaces the 6/10 critic bar)

- **Where today:** `app/agents/product_viability_critic.py` (single LLM call,
  1-10 scale), `config/settings.py:267` (`VIABILITY_CRITIC_MIN_SCORE=6`),
  consumed in `trend_research_agent.py:204-221`.
- **What's wrong:** a single cheap-model judgment on a 10-point scale with a
  6 threshold lets through exactly the "bland but not broken" concepts the live
  shop is now full of (avg 4.1/10 on re-audit). The critic sees the market data
  but nothing forces it to weigh competition, trend direction, timing, or
  originality — and one model call has high variance.
- **Fix — build `app/services/product_score_service.py`, a 0-100 composite
  score with a hard gate at `PRODUCT_MIN_SCORE=95` (env knob, enforced in
  code):**

  **A. Hard gates first (any hit → score = 0, reject, with reason as retry
  feedback).** These all exist — keep calling them exactly as today:
  - trademark screen (`app/core/trademark_screen.py`)
  - `occasion_mismatch()` seasonal gate (`app/core/seasonality.py`)
  - dedup vs recent shop products (`_dedup_error`)
  - format/strategy/multi-item validators (`_validate_product`)

  **B. Deterministic evidence points (0-40)** — computed from data the concept
  pipeline ALREADY attaches, zero new spend:
  - **Demand, 0-10:** from `interest_trend` (1-5): matched keyword direction
    rising=10, flat=6, falling=0. No matching trend keyword = 4 (unknown ≠ good).
  - **Competition, 0-10:** from `market.competition_count` (A-2):
    <1,000 → 10; 1k-10k → 8; 10k-50k → 5; >50k → 2. No market data → 4.
  - **Price/margin headroom, 0-10:** market `price_p50` vs the format's
    `price_band` (product_formats.py): p50 in the band's upper half → 10;
    lower half → 7; p50 BELOW band floor (race-to-the-bottom niche) → 2;
    no data → 5.
  - **Timing, 0-5:** evergreen concept → 5; in-window occasion → 5 if ≥6 weeks
    of window remain, 3 if less (late-window listing has less time to rank).
  - **Originality, 0-5:** max difflib similarity vs ALL recent shop titles
    (not just same-format like `_dedup_error` — cross-format near-dupes still
    cannibalize search): <0.45 → 5; 0.45-0.60 → 3; 0.60-0.75 → 1 (>0.75 is
    already a hard gate).

  **C. Independent LLM judgment (0-60):** run the existing critic rubric TWICE
  as independent calls — once on `CONCEPT_MODEL`, once on `DEFAULT_MODEL`
  (different models = genuinely independent judgment; temperature 0.3). Each
  returns 1-10; map each to 0-30 (score×3). Composite LLM points =
  **min(call1, call2) × 2 scaled** — i.e. use the HARSHER judge (taking the
  mean lets one generous call drag junk over the bar). Both judge prompts get
  today's date, in-window occasions, the market block, AND the deterministic
  subscores from (B) so they judge with evidence in hand.

  **D. Gate:** `total = B + C`; pass iff `total >= settings.PRODUCT_MIN_SCORE`
  (default **95**). On fail, the retry feedback must name the weakest axes
  ("scored 78: competition 2/10 — 84,000 rivals; demand 0/10 — 'graduation
  printable' is falling"), so the concept LLM's next attempt fixes the actual
  weakness. Bump `MAX_CONCEPT_ATTEMPTS` from 3 to 5 — a 95 bar needs more
  shots. If nothing clears 95 in a cycle, create NOTHING. Do not auto-lower
  the bar: the live-shop evidence shows mediocre listings actively hurt
  (shop-grid conversion, $0.20 fees, Etsy shop-quality signal). A skipped day
  is cheaper than a 60-point product.

  **E. Calibration + observability (do NOT skip this):** a 95/100 bar is
  near-perfection on every axis — enforced blind it may build zero products
  forever, or the LLM judges may drift generous until junk passes again. So:
  1. Record EVERY scored concept as a `concept_scored` analytics event with the
     full breakdown (deterministic subscores, both judge scores, total,
     passed). This is the tuning dataset.
  2. Ship in **shadow mode** first: `PRODUCT_SCORE_ENFORCE=false` computes and
     logs scores while the old 6/10 gate still decides. After ~5 days, look at
     the distribution (`GET /analytics/events?event_type=concept_scored`),
     confirm scores spread sensibly (the audit's obvious winners land 90+,
     bland ones land 60-80), then flip `PRODUCT_SCORE_ENFORCE=true`.
  3. Set `CONCEPT_MODEL=anthropic/claude-sonnet-5` (knob exists, B-5) — a
     mini-model cannot hold a 95-bar consistently, and concept+critic quality
     is the highest-leverage LLM spend in the system (~cents/day).
- **Verify:** unit tests with fixture concepts + stubbed judges: (a) rising
  demand + low competition + evergreen + original + two 9s → ≥95 passes;
  (b) same but one judge says 7 → fails; (c) falling keyword or >50k
  competition → fails regardless of judges; (d) any hard gate → 0. Live:
  5 days of `concept_scored` events, then enforce.

### 1-2. Clean the existing shop face — the audit report is actionable NOW
- **Where:** `audit_report.json` (repo root, untracked), live shop.
- **What:** 22 of 28 live listings score below the pass bar; several are for
  occasions that passed months ago. Every buyer who lands on ANY listing sees
  this grid — it suppresses conversion shop-wide and each active listing pays
  $0.20 auto-renew per 4 months.
- **Fix:** (a) deactivate everything scoring ≤3 in the report (score 1-3 =
  "would erode trust in the shop" per the critic's own rubric) — 8 listings;
  (b) let the 7-4 SEO refresh take one shot at the 4-5 scorers before deciding;
  (c) keep the 6s. Do it via a small script reusing
  `EtsyClient.update_listing(state="inactive")` reading the report JSON, with
  a dry-run mode; Maj eyeballs the list before apply. (d) Move the report to
  `instructions/audit_reports/2026-07-12.json` and commit it; re-run the
  script monthly (fold into the existing monthly prune tick).
- **Verify:** shop face shows only ≥4 scorers; report file committed.

### 1-3. `import os` is MISSING in pipeline_orchestrator — wall-art sets silently ship 3 copies of the SAME image
- **Where:** `app/services/pipeline_orchestrator.py` — module imports (lines
  56-76, no `os`) vs `os.replace(...)` at line 1063 inside
  `_stage_wall_art_set`, wrapped in `try/except Exception: pass`.
- **What (verified by reading the code path):** each set piece is generated to
  the same `design.png` path; the rename to `set_piece_{i}.png` raises
  `NameError: os` which the bare except swallows, so `piece_path` never
  changes and every subsequent generation OVERWRITES the previous piece. The
  final "set of 3 coordinated prints" is one image three times; the palette
  check trivially passes (identical images); content QA passes each
  individually. The moment `WALL_ART_SET_ENABLED` is flipped, every set ships
  broken — at 3x generation cost.
- **Fix:** add `import os` at module top; make the rename failure LOUD (log +
  fail the set) instead of `except: pass`; better, have
  `PODPipelineService.build_product_record` accept an output filename so no
  rename is needed at all.
- **Verify:** extend `scripts/test_step104_wall_art_set.py`: stub the pipeline,
  assert the 3 returned piece paths are DISTINCT files with distinct content
  hashes.

### 1-4. Content-QA regeneration DROPS the text overlay for text-led products
- **Where:** `pipeline_orchestrator.py` — initial design call at line 159
  passes `design_brief` (with the "NO text, leave center space" instruction)
  and `display_text=display_text`; the regeneration inside
  `_stage_content_quality` (line 417) calls
  `_stage_pod_design(task_id, product_name, visual_brief, task_type, report)`
  — plain brief, NO `display_text`.
- **What:** a text-led product (quote print, affirmation) that fails content QA
  once is regenerated WITHOUT the deterministic text overlay and WITHOUT the
  text-free-background instruction: the image model bakes its own (garbled)
  text in, or the product loses its words entirely. B-4 exists precisely to
  prevent this.
- **Fix:** pass `display_text` and the augmented brief through
  `_stage_content_quality` into the regen call (thread the same two args).
- **Verify:** unit test: text-led task, first QA fails, assert the regen call
  received `display_text` and the no-text brief.

### 1-5. Coloring-page "must be uncolored" is prompt-only — add the cheap deterministic check
- **Where:** `pipeline_orchestrator.py:943-950` (1-8 STRICT rules appended to
  the generation prompt); `_flatten_white_background` only whitens NEAR-white
  pixels; content QA (`content_quality_service.py:302`) reviews generically.
- **What:** the 1-8 rule relies on the image model obeying and a vision model
  noticing. A half-colored page (the exact live failure that motivated 1-8)
  can pass both. Whiteness is trivially checkable in code.
- **Fix:** after generation of a `coloring_page`, compute the fraction of
  pixels that are BOTH non-near-white AND non-near-black (i.e. actual color /
  grey shading) via PIL; if > ~3%, fail with a specific reason ("page is
  pre-colored") and regenerate (counts as a CONTENT_QA attempt). ~15 lines,
  $0.
- **Verify:** unit test with a synthetic half-colored page (fails) and a pure
  line-art page (passes).

### 1-6. Seasonal event table misses occasions the shop ALREADY sells — the gate can't reject what it can't name
- **Where:** `app/core/seasonality.py:54-99` (`_EVENTS`).
- **What:** the live shop has "Cinco de Mayo Fiesta" and "Stars and Stripes
  Summer" (July 4th) listings — neither occasion exists in `_EVENTS`, so
  `occasion_mismatch()` can NEVER reject a Cinco de Mayo concept proposed in
  December, `occasion_for()` never stamps them, and the 1-4 lifecycle never
  deactivates them. Also missing: Hanukkah, New Year's *Eve* party goods,
  wedding season, Diwali.
- **Fix:** add entries (key, date fn, windows, match keywords, seeds) for:
  july_4th (Jul 4; match "4th of july", "fourth of july", "independence day",
  "stars and stripes", "patriotic"), cinco_de_mayo (May 5; "cinco de mayo",
  "fiesta", "papel picado"), hanukkah (movable — computing it needs a Hebrew
  calendar table; a hardcoded {year: date} dict for 2026-2030 is fine and
  honest), weddings (season proxy: May 1 anchor, wide window; "wedding",
  "bridal shower", "bachelorette"). Events can be MATCH-ONLY (never seeded
  for building) — the gate still needs their keywords.
- **Verify:** unit tests: "Cinco de Mayo Fiesta Coloring Page" rejected on
  July 12; "4th of July BBQ Printable" rejected in December, accepted in
  early June.

### 1-7. Pre-STEP-104 listings have no `occasion` metadata — the seasonal lifecycle skips ALL of them
- **Where:** `seasonal_listing_service.py:59-60` filters on
  `metadata_["occasion"]`, which only autonomy tasks created AFTER 104-B carry.
- **What:** the 28 live listings (including the Easter/Mother's Day/Thanksgiving
  ones the audit flagged) predate the stamp — the weekly lifecycle tick will
  never deactivate them. The exact problem 1-4 was built to solve persists for
  the whole existing catalog.
- **Fix:** one-off backfill script: for every DONE product task, run
  `occasion_for(title, description)` (after 1-6 expands the table) and stamp
  `metadata_["occasion"]`. Then the existing weekly tick handles them forever.
- **Verify:** run backfill dry-run, eyeball the mapping, apply; next lifecycle
  tick reports deactivations for the out-of-season ones.

---

## TIER 2 — Learning loop & concept-path leftovers

### 2-1. Winner-variant tasks bypass EVERY concept gate — including seasonality
- **Where:** `etsy_receipt_worker.py:595-636` (`_maybe_spawn_winner_variant`)
  creates the task directly via `TaskService.create_task` — no viability
  critic, no trademark screen, no dedup, no `occasion_mismatch`, and (after
  1-1) no ≥95 score.
- **What:** someone buying a leftover Halloween printable in July immediately
  spawns a NEW Halloween product in July (the parent's `occasion` is even
  copied into the variant's metadata, but nothing checks its window). And a
  variant concept never faces the quality bar at all — the one path that
  creates products from the strongest signal has the weakest gate.
- **Fix:** in `_maybe_spawn_winner_variant`: (a) if parent metadata has
  `occasion` and `not occasion_in_window(occ)`, skip (log why); (b) after 1-1
  lands, route the variant through the scoring gate too — cleanest is to have
  the executor's output validated the same way, or generate the variant
  concept through `TrendResearchAgent._propose_product` with the parent as
  the insight.
- **Verify:** unit test: parent with `occasion=halloween`, today=July →
  no variant task created.

### 2-2. `seamless_pattern` is validate-able but never ADVERTISED to the concept LLM
- **Where:** `trend_research_agent.py:_build_concept_prompt` — the format menu
  lists single_print/coloring_page/greeting_card/phone_wallpaper/pdf/sticker
  (+gated POD/set), but `seamless_pattern` (a real format in PRODUCT_FORMATS,
  with a B-3 seamlessness check) appears only in the JSON `product_format`
  enum line.
- **What:** the model effectively never proposes it — a built, tested format
  (digital paper for crafters, decent niche) generates zero products.
- **Fix:** add a menu line ("seamless_pattern — a square, tileable repeating
  pattern / digital paper for crafters") or consciously remove the format.
- **Verify:** grep the prompt; watch one cycle's proposals over a week for at
  least one pattern concept.

### 2-3. `AnalysisAgent` is dead code; `synthesize()` always gets an empty analysis
- **Where:** `trend_research_agent.py:123` (`synthesize(research, "")`),
  `app/agents/market_intelligence/analysis.py` (never imported by the loop).
- **What:** the intelligence prompt carries an empty "Analysis:" section every
  cycle; the analysis agent is maintained but unused. Either wire it (one more
  LLM call — probably not worth it) or delete it and drop the parameter.
- **Fix:** delete `analysis.py` + the empty arg (keep `synthesize(research)`),
  or document why it stays.

### 2-4. Insights/prompt hygiene (small)
- `_RESEARCH_TOPIC` still says "and print-on-demand trending products" while
  POD is paused — steers research toward unbuildable products. Make the topic
  reflect `_proposable_formats()`.
- The critic's `market` object is attached AFTER dedup but the deterministic
  subscores (1-1) will want it — keep the current order (market → critic) when
  refactoring.

---

## TIER 3 — Marketing & distribution

### 3-1. Marketing refresh is STILL off — products get one Tumblr post ever
- **Where:** `MARKETING_REFRESH_ENABLED=false` (settings.py:234). Built,
  tested, capped since step 99; STEP 104 fixed the stale-listing weighting.
- **Fix (Maj, manual):** set `MARKETING_REFRESH_ENABLED=true` in Railway.
  There is no code reason left to keep it off.

### 3-3. Listing video built but off
- **Where:** `LISTING_VIDEO_ENABLED=false` (settings.py:181); 104-I(b) shipped
  the deterministic ken-burns renderer + upload.
- **Fix (Maj, manual):** flip it on for a week; watch publish latency/CPU on
  Railway. Etsy boosts listings with video.

### 3-4. Channel breadth backlog (after Pinterest)
- Tumblr is the only live channel. Once Pinterest is connected (Maj's manual
  item), the next cheap wins are Facebook Page + Instagram via the Meta Graph
  API (same watermarked mockups, same MarketingService pattern). Backlog, not
  urgent.

---

## TIER 4 — Money math

### 4-1. P&L still ignores renewal fees and offsite ads
- **Where:** `revenue_service.record_fee_estimate` covers transaction+payment
  per sale; `dashboard.py:/pnl` nets those. The $0.20 auto-renew per active
  listing per 4 months and the 12-15% offsite-ads fee (when attributed) appear
  nowhere. 28 active listings ≈ $17/year renewals today, growing with the
  catalog; at 300 listings it's ~$180/year.
- **Fix:** (a) monthly tick (fold into the existing monthly prune report):
  `active_listing_count × $0.20 / 4` recorded as a `fee_estimate` event with
  basis "renewal"; (b) the real fix from 104-4-1 remains: poll Etsy's
  payment-account ledger (`GET /shops/{shop_id}/payment-account/ledger-entries`)
  daily and record ACTUAL fees — offsite ads then shows up automatically.
- **Verify:** unit test the renewal math; compare one month of ledger entries
  vs the Etsy dashboard by hand once.

### 4-2. Circuit-breaker alert can spam once tripped
- **Where:** `autonomy_service.py:115-129` — `assert_within_circuit_breaker`
  calls `_alert_cap_hit` on EVERY refused call; a retry loop past the ceiling
  fires a Discord alert per attempt.
- **Fix:** once-per-day marker file like `_alert_ban_once_per_day` in
  trend_data_service (the pattern already exists — reuse it).
- **Verify:** unit test: two consecutive breaker hits → one alert.

### 4-3. Expect fewer products/day after the 95 gate — that's the point
- With `PRODUCT_MIN_SCORE=95`, expect 0-3 products/day instead of 10.
  Daily spend drops accordingly ($0.20 listing fee + ~$0.30 image spend per
  product actually built). Do NOT raise MAX_TASKS_PER_DAY to compensate;
  revisit knobs after 30 days of `concept_scored` data (1-1E).

---

## TIER 5 — Technical robustness

### 5-1. Crash between create_listing and the COMPLETED stamp duplicates the listing on resume
- **Where:** `pipeline_orchestrator.py` — `mark_pipeline_completed` fires at
  line 315-319 only after ALL stages; `_resume_incomplete_pipelines`
  (main.py:73) re-runs `run_post_completion` from scratch for DONE tasks with
  no `pipeline_status`.
- **What:** a crash after `create_draft_listing` succeeded but before the
  stamp → resume regenerates the delivery asset (new spend) AND creates a
  SECOND Etsy listing (+$0.20, duplicate content in search). Bounded (≤5
  tasks/6h) but real.
- **Fix:** persist the listing_id into `output_data` immediately after the
  readback-verified create (a tiny `task_service` write inside
  `_stage_create_listing`); on entry, `run_post_completion` checks for an
  existing listing_id and skips straight to the attach/publish stages against
  it instead of re-creating.
- **Verify:** unit test: task with `output_data.listing_id` set + no
  pipeline_status → resume does NOT call create_draft_listing again.

### 5-2. Receipt-worker state file writes are not atomic
- **Where:** `etsy_receipt_worker.py:715-717` — plain `write_text`, unlike the
  spend ledger's temp-file + `os.replace` (5-1 in STEP 104).
- **What:** a crash mid-write corrupts `receipt_worker_state.json` → `_load_state`
  returns `{}` → `last_checked_at=0` → next poll re-fetches the entire receipt
  history (idempotent, but a burst of API calls) and loses `failed_receipts`
  retry counts AND all the daily-tick timestamps (backup/stats/cleanup all
  re-run immediately).
- **Fix:** copy the ledger's atomic-write pattern (~4 lines).

### 5-3. Circuit-breaker check in the image provider only catches ImportError
- **Where:** `openrouter_image_provider.py:73-77` — `try: ...
  assert_within_circuit_breaker() except ImportError: pass`.
- **What:** intended: SpendCapExceeded propagates (correct). Unintended: any
  OTHER error from AutonomyService (disk full, corrupt ledger JSON path,
  permissions) also propagates and kills every image generation — the wallet
  guard becomes a single point of failure for the whole pipeline.
- **Fix:** `except SpendCapExceeded: raise` / `except Exception: pass` (log
  the swallow). Keep the breaker loud, make the plumbing failures soft.
- **Verify:** unit test: AutonomyService stubbed to raise OSError → generation
  proceeds; stubbed to raise SpendCapExceeded → generation refuses.

### 5-4. `_pin_from_mockup` can pick the previous pin (or any stale asset) as its source
- **Where:** `pipeline_orchestrator.py:1594-1612` — takes the FIRST existing
  listing asset; on a re-run/resume, `pin.png` itself (use_case="pinterest")
  can already be in the catalog. `_mockup_source` (video) has the same
  pattern.
- **Fix:** filter to `use_case == "listing"` and exclude `pin.png`/`video.mp4`
  filenames in both helpers.

### 5-5. Frontend will break for mutating actions once FACTORY_API_KEY is set
- **Where:** `frontend/index.html` fetches; `app/api/auth.py` protects every
  POST/PUT/PATCH/DELETE.
- **What:** the dashboard's read tiles stay open (by design), but any UI
  button that POSTs (task creation, prune, resubmit) will 401 the moment the
  key is finally set — which is a Tier-6 security must-do. This foot-gun
  discourages ever setting the key.
- **Fix:** tiny addition to the frontend: a key field stored in
  localStorage, sent as `X-Factory-Key` on every fetch. ~15 lines of JS.
- **Verify:** set a key locally, exercise a POST from the UI.

---

## TIER 6 — Repo housekeeping

1. **The virtualenv is committed:** `git ls-files venv | wc -l` → **1,611
   files** (83% of the tracked tree). `.gitignore` has `venv/` but the files
   predate it. `git rm -r --cached venv` + commit. Shrinks every clone/CI
   checkout and stops dependency noise in diffs.
2. **Merge `audit-step102-fixes` → `main`.** ~30 commits (all of STEP 103 +
   104) live only on the working branch; `main` is months stale. CI already
   runs on both. After merging, drop the branch from the CI trigger list.
3. **`audit_report.json` untracked in the repo root** — move to
   `instructions/audit_reports/2026-07-12.json` and commit (it's the evidence
   base for 1-2); add the root path to .gitignore so future runs don't clutter.
4. **Lying comments:** pipeline step 2.6 + MockupService docstring say
   "watermarked" — fix alongside 3-2.

---

## TIER 7 — Still-open manual actions (Maj — none need code)

Carried forward; several are security-relevant and OLD. Check each off:

1. **Rotate the Etsy + Tumblr OAuth tokens** (appeared in git history in
   step 102 — still treat as leaked until rotated). Do first.
2. **Set `FACTORY_API_KEY`** in Railway (do 5-5 first so the UI keeps working).
3. **Configure `BACKUP_S3_*`** — backups still live only on the same volume
   they protect; the weekly nag alert is firing for a reason.
4. **Enable `MARKETING_REFRESH_ENABLED=true`** (3-1) and, when ready,
   **`LISTING_VIDEO_ENABLED=true`** (3-3).
5. **Set `CONCEPT_MODEL=anthropic/claude-sonnet-5`** (needed for 1-1's judges).
6. **Pinterest** — known, deliberately out of scope for this audit.
7. **Printify production partner** in Etsy Shop Manager +
   `ETSY_PRODUCTION_PARTNER_ID` (before POD is re-enabled).
8. **Shop sections** (`scripts/create_shop_sections.py`) + `SHOP_SECTION_MAP`.
9. **Shop-level conversion surfaces** (~1h once): banner, about section with
   the AI disclosure mirrored, announcement, policies/FAQ, featured listings.
10. **Monthly Etsy sale/coupon** (not exposed via API v3) — strongest built-in
    urgency lever, pairs with each occasion window.
11. **Review + apply the 1-2 shop cleanup dry-run list** when the script is
    ready.

---

## Suggested working order

| Step | Items | Type |
|------|-------|------|
| 105-A | 1-3, 1-4, 1-5 (pipeline correctness: os import/set clobber, text-led regen, coloring-page whiteness) | code, small |
| 105-B | 1-1 (ProductScoreService + shadow mode + `concept_scored` events) | code, the big one |
| 105-C | 1-6, 1-7 (event table expansion + occasion backfill) | code |
| 105-D | 1-2 (shop cleanup script from audit_report.json, dry-run → Maj applies) | code + manual |
| 105-E | 2-1, 2-2, 2-3 (variant gates, seamless_pattern menu, dead analysis agent) | code |
| 105-F | 5-1 … 5-5 (resume dupes, atomic state, breaker except, pin source, UI key) | code |
| 105-G | 3-2 (real watermarks) + 4-1 (renewal fees / real fee ledger) + 4-2 | code |
| 105-H | Tier 6 housekeeping (venv, merge to main, commit report) | code/git |
| 105-I | Flip PRODUCT_SCORE_ENFORCE after 5 days of shadow data; Tier 7 checklist | manual (Maj) |

Everything in Tiers 1-5 is testable offline with the existing deterministic-
suite pattern (`tests/test_deterministic_suites.py`); nothing needs new paid
APIs. The single highest-leverage change in this file is 105-B: the factory
already knows how to build and ship products reliably — it now needs to refuse
to build the ones that aren't worth buying.
