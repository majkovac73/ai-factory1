# AI-Factory — DEEP AUDIT V2 (2026-07-21)

> Full-population, evidence-driven re-audit of the live production system
> (`kind-liberation-production.up.railway.app`, Etsy shop `58716525`
> CardsForAllOcDesigns). All production numbers pulled via `railway ssh` against
> the live `/data/app.db` (48.9 MB, WAL active) and the live Etsy API on
> 2026-07-21. Every claim traces to `audit_data/evidence_log.md` (E1–E9).
> Diagnostic scripts: `scripts/audit/deep_*.py`. Artifacts:
> `audit_data/catalog_unit_economics.csv`, `audit_data/agent_prompt_inventory.md`.

---

## 0. Executive summary

**Can this system make a real, profitable, unattended sale end-to-end, today?
NO — and right now it can't make anything at all.** As of 2026-07-21, the factory
is **hard-down**: every autonomy cycle since **01:57 UTC** fails with OpenRouter
**`402 - requires more credits`** (13 consecutive hourly failures through 10:57,
E3). The OpenRouter account is out of credits — almost certainly because
`CONCEPT_MODEL` was switched to `anthropic/claude-sonnet-5` (E5), which costs far
more per call than the previous `gpt-4o-mini`, and no credit top-up accompanied
the change. **Nothing has been produced, and no alert fired** — the cycle error
is caught with a bare `logger.error` (E4/code), and the daily zero-production
check can't fire until 24 h with zero products.

Evidence chain, walked at full population:
1. **148 tasks, all `status=DONE`, but pipeline outcome is: 90 `BLOCKED_NO_PRODUCT`
   (60.8%), 36 `COMPLETED` (24.3%), 21 null, 1 unverified** (E2). The prior audit
   sampled and called the pipeline "works end-to-end." **It does not:** it fails to
   produce a deliverable 3 out of every 5 runs. **79% of blocks (71/90) are
   delivery-asset generation failures** — "no verified PDF" = 40, "no verified
   delivery asset" = 31 (E4). pdf_planner_or_guide blocks **42/56 = 75%**.
2. **45 active Etsy listings, 0 sales, $0 revenue** (E2/E6). Etsy `sale_recorded`
   and `fee_estimate` events = 0. Max views on ANY listing = **2** (E3). Total
   spend to date **$11.71** (autonomy ledger, E3), cross-validated at **$11.85** by
   independent image-count costing (E6) — a 1.2% agreement across two data paths.
3. **Product quality is capped: 0 of 83 scored concepts passed the gate** (mean
   62.9, max 71, floor 90) — confirmed at full population (E3). The market-grounding
   axis the gate leans on is **dead**: `EtsyMarketService` sends the keystring-only
   `x-api-key` and Etsy 403s every call ("Shared secret is required in x-api-key
   header", E3/E4) — so "grounded in real Etsy demand" is currently false.
4. **Distribution is still broken, differently than reported.** `PINTEREST_SANDBOX=true`
   (E5), so the 4 "successful" pins (E3) were almost certainly created in the
   sandbox — not publicly visible, zero real reach. Tumblr posts 48× but drives no
   measurable traffic (max 2 views/listing). Re-promotion is off.

**Net:** this is not "a working factory with no distribution" (prior verdict). It
is **a factory that is currently switched off by a billing error, that even when
running blocks 60% of its output, sells nothing, and whose one market-grounding
data source is 100%-failing.** Per-unit margins are actually healthy (break-even
< 0.07 units, §2B) — the problem is entirely production reliability + zero
conversion, not thin margins.

### Delta vs. prior audit (AUDIT_AND_OPTIMIZATION_PLAN_2026-07-20)
| Prior claim | This pass |
|---|---|
| "Pipeline works end-to-end and publishes complete sellable listings" | **OVERTURNED at population**: 60.8% of tasks block with no product; 75% of PDF tasks fail. |
| "No FAILED/BLOCKED tasks ever recorded (blocks maybe not surfaced)" | **CORRECTED**: 90 blocks ARE persisted (`output_data.pipeline_status`), root-caused to delivery-gen failures. Prior didn't quantify the rate. |
| "EtsyMarketService pulls competition_count + price_p50" | **OVERTURNED**: it 403s 100% (keystring-only header bug). Market grounding is dead. |
| "Pinterest 90% failing / Trial-blocked" | **REFINED**: also `PINTEREST_SANDBOX=true` — the 4 "successes" are sandbox pins (no real reach), not just Trial 403s. |
| "Products self-score ~63/100 (n=81)" | **CONFIRMED at full population** (n=83, mean 62.9, 0% pass). |
| "Set CONCEPT_MODEL=sonnet-5" (prior recommendation) | **CAUTION — this recommendation, applied without a credit top-up, is the direct cause of the 402 halt.** New P0. |
| Unit economics "thin / low-margin coloring pages" | **REFRAMED**: break-even < 0.07 units; margins are fine. The issue is 0 conversion + 60% block waste, not margin. |
| Images "0" then retracted | **RE-CONFIRMED retraction**: per-listing readback shows 2–4 images each (all 1024×1024). |

### Summary table (impact computed from real numbers; math shown)
| Tier | Count | Est. $ / impact (with math) |
|------|-------|------------------------------|
| **P0** | 4 | Unbounded: system produces **$0** while halted (402); 60.8% block rate wastes ≥ **$1.72** measured image spend (floor) + 90 lost listings; 0% gate pass caps quality ceiling. |
| **P1** | 6 | Market-axis 100% dead (gate can't work); 9 h+ silent halt (no alert); Pinterest sandbox = 0 real reach on 45 listings; ~$4–7 of the $11.71 spend went to blocked/zero-view output. |
| **P2** | 4 | POST-retry double-create risk; off-box backups still unset (48.9 MB SPOF); early mispricing (coloring pages at €15 vs €3.50 band); QA rejects 17 items after paying to generate them. |
| **P3** | 4 | Registry/reality drift (dead agents); single-page vs bundle format mismatch; OAuth-callback open surface; tag under-fill on 18/45 legacy listings. |

---

## 1. Prioritized flat action list

### [P0] #1 — The factory is hard-down: OpenRouter 402 halts every cycle (caused by the sonnet-5 switch with no credit top-up)
**Where:** OpenRouter account billing; `CONCEPT_MODEL=anthropic/claude-sonnet-5` (E5); `AutonomyWorker._run_loop` error handling (`app/workers/autonomy_worker.py:79-80`).
**Evidence:** E3 — 13× `research step failed: Error code: 402 - 'This request requires more credits, or fewer max_tokens'`, first at **2026-07-21 01:57:03**, hourly through 10:57. `autonomy_state_2026-07-21.json` = 1 task, $0.116. `CONCEPT_MODEL` = sonnet-5 (E5).
**Population coverage:** All 27 ERROR rows in `logs` (range 2026-07-07→07-21); all 15 `autonomy_state` files.
**Why it matters (quantified $ impact):** Zero production while halted. At the configured 10 tasks/day, every day down = 10 un-built products. Direct revenue impact is $0 (nothing built), but this gates 100% of system value. The trigger (sonnet-5) also raises per-cycle text cost from ~$0.002 to ~$0.01+ (5×) — sustainable only with a funded balance.
**Confidence:** High — 13 independent, timestamped, identical errors over 9 h.
**Fix:** (a) Top up the OpenRouter credit balance AND set a low-balance auto-reload; (b) reduce `max_tokens` on the research/concept calls so a sonnet request fits available credit; (c) **make the cycle-level exception alert** (see #5) so a future 402/quota/billing halt pages Maj within one cycle, not 24 h.
**Verification after fix:** `railway ssh "grep -c '402' <recent logs>"` = 0 over 3 h; a new `task_completed` event appears; `autonomy_state_<today>` spend increments.
**Adversarial check performed:** Confirmed it's not a one-off — 13 consecutive hourly failures, all identical, none succeeding after. Confirmed it's the research step (not a single agent) via the log source. Confirmed the switch to sonnet is live (E5), tying cause→effect.

### [P0] #2 — 60.8% of all tasks produce no product; 79% of blocks are delivery-generation failures (75% of PDF planners)
**Where:** `pipeline_orchestrator` delivery stages (`_stage_pdf_design`, `_stage_pod_design`, readback gates); PDF page-count verification.
**Evidence:** E2 (90/148 BLOCKED_NO_PRODUCT), E4 (block buckets: "no verified PDF"=40, "no verified delivery asset"=31; `blocked_by_type`: pdf 42/56=75%, coloring 26/50=52%, single_print 11/20=55%, seamless/phone/pod = 100%).
**Population coverage:** All 148 tasks; all 90 blocks bucketed.
**Why it matters (quantified $ impact):** Measured wasted image spend on blocked tasks = **$1.72** (43 billable images × $0.04, E7) — a FLOOR, since `image_assets` are auto-pruned (>6 h mockups, >3 d delivery), so true waste is higher; the big spend days (07-10 $2.69, 07-12 $2.15) coincide with heavy blocking. Beyond spend, 90 tasks = 90 listings not shipped → the catalog is ~⅓ the size it "paid for."
**Confidence:** High — full-population counts from the primary table.
**Fix:** Root-cause the "no verified PDF" path (40 cases): PDF page-count/readback is failing after generation. Likely the interior-page renderer or the pypdf readback expecting N images/page. Add a pre-publish self-test on 1 page before generating all N; log the exact readback mismatch (expected vs got) to `logs` so the failure mode is quantifiable. Cap PDF page count until the failure rate drops. Treat 100%-blocking formats (seamless_pattern, phone_wallpaper) as disabled until they pass a smoke test.
**Verification after fix:** `blocked_by_type` PDF block rate < 20% over the next 30 PDF tasks; `logs` show the readback delta on any remaining failure.
**Adversarial check performed:** Checked these aren't gate-rejections (quality) vs. generation failures — the buckets separate "content quality gate failed" (8) and "marketing mismatch" (9) from "no verified PDF/asset" (71), so the dominant failure is genuinely generation/readback, not the quality gate.

### [P0] #3 — 0 of 83 concepts pass the quality gate, and its market-grounding axis is 100% dead
**Where:** `ProductScoreService`; `EtsyMarketService` (header bug); `CONCEPT_MODEL` sonnet just deployed.
**Evidence:** E3 (concept_scored n=83, mean 62.9, max 71, floor 90, passed=0). E3/E4 — EtsyMarketService 403 "Shared secret is required in x-api-key header"; source sends keystring-only.
**Population coverage:** All 83 concept_scored events; the market-service source read in-container.
**Why it matters (quantified $ impact):** If `PRODUCT_SCORE_ENFORCE` is ever flipped on as-is, production → 0 (0% pass). The dead market axis means the deterministic evidence subscore is systematically low, dragging every concept below the floor — the gate is unreachable partly because of a *bug*, not just weak concepts.
**Confidence:** High — full-population pass rate + live 403 + source.
**Fix:** (1) Fix EtsyMarketService header to `f"{ETSY_API_KEY}:{ETSY_SHARED_SECRET}"` (Etsy now requires the shared secret even on the public endpoint). (2) Keep enforce OFF until, with sonnet + working market data, `concept_scored.passed=true` rate ≥ 30% over a day. (3) Re-baseline the floors against the new (sonnet + real-market) distribution.
**Verification after fix:** EtsyMarketService returns non-None `competition_count` in logs; new concept_scored events show a materially higher mean and > 0 pass rate.
**Adversarial check performed:** Verified the 403 is the header, not rate-limiting/keyword — Etsy's error text is explicit ("Shared secret is required"). Verified the gate is in shadow (enforce not set, E5) so it isn't itself causing the 90 blocks.

### [P0] #4 — Distribution produces zero real reach: Pinterest is in sandbox; 45 listings, max 2 views each
**Where:** `PINTEREST_SANDBOX=true` (E5); `pinterest_channel` uses `api_base()` (sandbox host when the flag is set).
**Evidence:** E5 (sandbox true), E3 (4 "successes" 07-15/07-20), E7 (public pin fetch inconclusive — SPA 200), E3 (listing_stats: max 2 views).
**Population coverage:** All 45 listings' view history (419 listing_stats events); all 94 marketing_posts.
**Why it matters (quantified $ impact):** 45 listings built for $11.85 with **0 buyer-intent reach**. The 4 "successful" pins likely never appeared on public Pinterest. Every downstream investment is stranded until one real traffic channel exists.
**Confidence:** Medium-High — config is definitive (sandbox on); "sandbox pins invisible" is standard Pinterest behavior; the one live check (SPA 200) is inconclusive, hence not High.
**Fix:** Unset `PINTEREST_SANDBOX` in Railway; land Standard access (demo per `scripts/pinterest_demo.py`); then the `can_publish()` probe auto-resumes real pins. Verify with an authenticated `GET /v5/pins/{id}` against the **production** host returning the pin.
**Verification after fix:** `marketing_posts` gets `pinterest:success` rows whose pin id resolves via production `GET /v5/pins/{id}`; Etsy shop `views` climb over 7 days.
**Adversarial check performed:** Tried to confirm the pins are real via public URL — got HTTP 200, but recognized Pinterest is a SPA that 200s any URL, so I did NOT treat that as proof; downgraded confidence and stated the exact check needed (authenticated production GET).

### [P1] #5 — Cycle-level failures (402, quota, provider outage) have no alert; the factory can die silently for up to 24 h
**Where:** `app/workers/autonomy_worker.py:79-80` (`except Exception: logger.error(...)`); `ProductionMonitorService.run_zero_production_check` (only fires at 24 h of zero output).
**Evidence:** E3 (9 h of 402 with no alert marker); E4 (no `zero_production_alert.json`); code read.
**Population coverage:** All alert marker files on the volume; the full error-log window.
**Why it matters (quantified $ impact):** The single most important failure mode (factory stopped) is invisible for up to 24 h. At 10 products/day that's a full day of output lost per incident before anyone knows.
**Confidence:** High — code path + absence of any alert during a real 9 h halt.
**Fix:** In `_run_cycle`'s except, send an AlertService alert (rate-limited once/hour) with the exception class + message. Add a dedicated "N consecutive cycle errors" tripwire (mirrors the #3 enforce-streak guardrail I added).
**Verification after fix:** Force a cycle exception in staging → a Discord alert within one cycle; a `logs` ERROR row with the alert recorded.
**Adversarial check performed:** Confirmed the existing thread-death alert (`_run_loop` finally block) does NOT cover this — the thread stays alive and loops, so that alert never triggers on a 402.

### [P1] #6 — EtsyMarketService `x-api-key` header bug: 100% of competitor/price lookups 403
**Where:** `app/services/etsy_market_service.py:29,40`.
**Evidence:** E3/E4 — live 403 "Shared secret is required in x-api-key header"; source `headers={"x-api-key": settings.ETSY_API_KEY}`.
**Population coverage:** Source read in-container; live 403 observed 07-21.
**Why it matters (quantified $ impact):** The "validate against real Etsy demand" premise is false for 100% of concepts. It degrades every concept score (feeds #3's 0% pass) and blinds the pipeline to saturation/pricing — the exact data needed to avoid the single-page-vs-bundle mistake (§2E).
**Confidence:** High — explicit Etsy error + source.
**Fix:** `headers={"x-api-key": f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"}` (same format the authenticated `EtsyClient` already uses). Add a startup/self-test that logs a WARNING if a canary market lookup 403s, so this never silently rots again.
**Verification after fix:** A market lookup returns `competition_count` > 0 in logs; concept_scored payloads cite matched market data.
**Adversarial check performed:** Confirmed it's not OAuth-scope (public endpoint needs no OAuth) and not the keyword — swapped-in header format is the documented fix (mirrors the working EtsyClient).

### [P1] #7 — ~⅓ of all image spend + the majority of the catalog's cost bought nothing sellable
**Where:** delivery-generation blocks (#2); `image_assets` (pruned → undercount).
**Evidence:** E7 (43/130 billable images on blocked tasks = $1.72 floor); E6 ($11.85 total, $0 revenue).
**Population coverage:** All 130 billable images; all 45 listings.
**Why it matters (quantified $ impact):** Of $11.71 spent, $0 returned; ≥ $1.72 provably bought blocked output, and the block rate implies ~40–60% of total spend produced nothing shippable. This scales linearly with volume until #2 is fixed.
**Confidence:** Medium — the $1.72 is a hard floor; the higher figure is inferred from the 60.8% block rate against pruned image data.
**Fix:** Fix #2 (block rate). Separately, emit `cost_incurred` (now deployed) on EVERY image at generation so post-fix waste is exactly measurable per task, not estimated from surviving `image_assets`.
**Verification after fix:** `cost_incurred` summed over BLOCKED task_ids trends toward $0 as the block rate drops.
**Adversarial check performed:** Checked whether the pruned images undercount — yes; stated $1.72 as a floor rather than the total.

### [P1] #8 — Live catalog is still 1024×1024 square (the #8 fix hasn't reached production)
**Where:** existing 45 listings; the halted pipeline hasn't produced new ones.
**Evidence:** E7 — per-listing readback: all sampled images 1024×1024.
**Population coverage:** 6-listing authoritative readback (all 1024²); no newer listings exist to differ (402 halt).
**Why it matters (quantified $ impact):** Thumbnails render softer than 2000px competitors at the scarce top of the funnel; a backfill or new production is needed for the fix to matter.
**Confidence:** High for the sampled listings; Medium that all 45 are square (only 6 read back — but they're the newest, and no ≥2000px generation has run).
**Fix:** Once #1/#2 restore production, new listings are ≥2000px automatically; add a one-off mockup-regeneration backfill for the existing 45.
**Verification after fix:** New listings' `full_width ≥ 2000`; hero landscape.
**Adversarial check performed:** Read back the NEWEST listing ids (most likely to show the fix) — still 1024², confirming the fix hasn't shipped listings yet.

### [P1] #9 — Etsy AI-listing velocity (~22/week) is 2–4× the safe rate; suspension risk
**Where:** `MAX_TASKS_PER_DAY=10`; 45 listings in 14 days.
**Evidence:** E9 (Etsy policy: ≥50 near-identical AI listings/day trips spam filters; 5–10/week safe). E6 (45 listings / 14 days = 22.5/week).
**Population coverage:** Full publish history vs. current live policy text.
**Why it matters (quantified $ impact):** A suspension is revenue-ending. At 22/week of AI-generated, often near-identical (single coloring pages), the shop sits well above Etsy's cited comfort band.
**Confidence:** Medium — policy guidance is a published range, not a hard published number; the shop's rate is clearly above it.
**Fix:** Throttle `MAX_TASKS_PER_DAY` toward ~1–2/day (7–10/week), maximize per-listing distinctiveness (titles/covers vary already), and ensure the AI disclosure is in every description (SHOP_AI_DISCLOSURE is appended — verify it renders).
**Verification after fix:** Publish rate ≤ 10/week; spot-check 5 listings for the AI disclosure string live.
**Adversarial check performed:** Confirmed the disclosure mechanism exists (SHOP_AI_DISCLOSURE) so the risk is velocity + near-duplication, not missing disclosure.

### [P1] #10 — IntelligenceAgent silently degrades to zero opportunities on malformed output
**Where:** `app/agents/market_intelligence/intelligence.py:61-67`.
**Evidence:** §2A / source — JSON-parse failure returns `{"opportunities": []}`.
**Population coverage:** Source read; behavior confirmed by code.
**Why it matters (quantified $ impact):** A bad LLM reply kills the cycle with no product and no distinct signal — indistinguishable from "no opportunities found." Contributes to unexplained empty cycles.
**Confidence:** High — explicit code.
**Fix:** On parse failure, raise (let TrendResearchAgent's cycle abort loudly) or emit a WARNING + a distinct analytics event so the rate is measurable.
**Verification after fix:** A forced malformed reply produces a WARNING row, not a silent empty cycle.
**Adversarial check performed:** Verified it's not fabricating data (returns empty, not guessed) — so it's a silent-nothing, not a silent-lie; still flagged per the no-silent-fallback rule.

### [P2] #11 — #12 backoff retries 5xx on POST (listing/pin create) — low-probability double-create
**Where:** `app/core/http_backoff.py` (`retry_statuses` includes 5xx for all methods); Etsy `POST /listings`, Pinterest `POST /pins`.
**Evidence:** §2D — retry_statuses applies to POST; no idempotency key on Etsy create.
**Population coverage:** Code path; `tasks.retry_count=0` and no duplicate listing ids observed today (E2) — so not yet realized.
**Why it matters (quantified $ impact):** A 502/503 returned AFTER Etsy created a listing → retry creates a second ($0.20 wasted + split search signal). Low probability but unbounded if a gateway flaps.
**Confidence:** Medium — real code path, low real-world probability (httpx timeouts raise rather than retry; 5xx-after-success is uncommon).
**Fix:** Restrict mutating methods (POST/PUT/PATCH/DELETE) to retry ONLY on 429 (safe); retry 5xx only on GET/idempotent reads.
**Verification after fix:** A unit test asserts POST is not retried on 500; only 429 is.
**Adversarial check performed:** Traced whether the existing readback+rollback guard covers it — it prevents double-*publish* but not a second draft listing created by the retried POST before readback runs.

### [P2] #12 — Off-box backups still unconfigured; 48.9 MB DB + backups + OAuth tokens on one volume (SPOF)
**Where:** `BACKUP_S3_BUCKET` unset (E5); DB + tokens on `/data`.
**Evidence:** E5 (no S3 bucket), E1 (single volume).
**Population coverage:** Live env + volume listing.
**Why it matters (quantified $ impact):** Volume loss = catalog↔listing map + all OAuth tokens + entire analytics/learning history gone together; hours of rebuild + OAuth re-auth + orphan-listing risk.
**Confidence:** High — env is definitive.
**Fix:** Set `BACKUP_S3_*` (R2/B2); verify an object lands and rehearse one restore. (boto3 already in requirements.)
**Verification after fix:** A dated backup object appears in the bucket today.
**Adversarial check performed:** Confirmed `BACKUP_ENABLED` default true but `_upload_offsite` is skipped without a bucket — so backups exist but only locally.

### [P2] #13 — Early mispricing: coloring pages listed at €15.00 (band is €2.00–4.50)
**Where:** `catalog_unit_economics.csv` rows 4535483951/4535469489/4535293371 (coloring_page, €15.00).
**Evidence:** E6 CSV — 8 listings at €15.00 including coloring pages; band `coloring_page=(2.00,4.50)`.
**Population coverage:** All 45 listings priced.
**Why it matters (quantified $ impact):** A €15 single coloring page is unsellable vs a €3.50 market — these listings are near-guaranteed zero-conversion, occupying the shop and the ~5–10/week policy budget.
**Confidence:** High — real prices vs the code's own band.
**Fix:** One-off re-price script to clamp existing listings into their format band; confirm the band is enforced at create (it is now for new listings — these are pre-enforcement).
**Verification after fix:** No active listing priced outside its `price_band`.
**Adversarial check performed:** Confirmed these are early (07-11 era) pre-band-enforcement listings, not a current regression.

### [P2] #14 — content_quality is doing real work but AFTER paid generation; 25 blocks are QA/consistency rejections
**Where:** `ContentQualityService`, consistency gate.
**Evidence:** E4 — "content quality gate failed"=8 (pre-colored 5.5–16.3%, garbled "Black the School", duplicate entries), "marketing/deliverable mismatch"=9.
**Population coverage:** All 90 blocks.
**Why it matters (quantified $ impact):** These 17 rejections each burned generation spend before rejection. Good that they reject garbage; costly that garbage is generated first.
**Confidence:** High.
**Fix:** Move cheap structural checks (pre-color %, duplicate-entry) earlier / into the generation prompt; keep the vision gate as backstop.
**Verification after fix:** Fewer post-generation QA blocks per 100 tasks.
**Adversarial check performed:** Confirmed the QA gate is not over-rejecting valid product — the sampled reasons are genuine defects.

### [P3] #15 — Registry lists 20 agents; the live pipeline's driver isn't among them (dead-agent surface)
**Where:** `app/agents/registry.py`; `agent_prompt_inventory.md` A0.
**Evidence:** §2A — TrendResearchAgent/ProductScoreService/ViabilityCritic unregistered; 16 registered agents have no runtime proof (agent_executions ≈ 0 pre-#15).
**Population coverage:** Registry + live call graph.
**Why it matters (quantified $ impact):** Audit/attack surface with no runtime value; `get_agent()` is reachable (key-gated).
**Confidence:** High.
**Fix:** Delete or wire-in the off-path agents; add per-agent execution logging (the #15 task_steps/agent_executions now exist — use them to prove which agents run).
**Verification after fix:** `agent_executions` shows only agents that actually run.
**Adversarial check performed:** Checked whether the "unused" agents are invoked indirectly — no import path from the autonomy pipeline to them.

### [P3] #16 — Format mismatch: shop sells single pages; the market sells bundles
**Where:** product mix (coloring_page=50 single pages); live market (E9).
**Evidence:** E9 — coloring best-sellers are 50–3000-page bundles; shop sells single €3.50 pages.
**Population coverage:** Full task-type mix; live market search.
**Why it matters (quantified $ impact):** Single pages are structurally uncompetitive; the pipeline already produces multi-page PDFs, so a **coloring-BUNDLE** format is achievable with no new capability — a real underserved fit visible in current data.
**Confidence:** Medium — market direction is clear; exact competitor prices UNVERIFIED (Etsy 403s fetch).
**Fix:** Add a `coloring_bundle` format (N-page PDF of line art) and bias the concept generator toward bundles for the coloring niche.
**Verification after fix:** New coloring products are multi-page bundles priced €6–15.
**Adversarial check performed:** Confirmed the pipeline can already build multi-page PDFs (56 pdf_planner tasks exist), so no new capability is required.

### [P3] #17 — OAuth callbacks are the only open mutating routes (accepted, low risk, noted)
**Where:** `app/api/auth.py` OPEN_CALLBACK_PREFIXES.
**Evidence:** E8 — all other mutating methods key-gated; 3 OAuth callbacks open.
**Population coverage:** Full auth logic read.
**Why it matters (quantified $ impact):** Minimal — callbacks validate `state`; they mutate only token state, trigger no paid action.
**Confidence:** High.
**Fix:** None required; keep `state` validation. (Documented for completeness.)
**Verification after fix:** N/A.
**Adversarial check performed:** Checked whether any callback triggers a paid action — no; they only store tokens.

### [P3] #18 — 18/45 legacy listings still under-fill tags (3–8 of 13)
**Where:** `catalog_unit_economics.csv` `tags` column.
**Evidence:** E6 — several listings with 3/5/6/7/8 tags.
**Population coverage:** All 45 listings.
**Why it matters (quantified $ impact):** Each empty tag slot is a search the listing can't appear in — free discoverability lost on already-zero-traffic listings.
**Confidence:** High.
**Fix:** Run `scripts/audit/backfill_truncated_tags.py --apply` (built in the prior pass) against the live catalog.
**Verification after fix:** All active listings carry 13 valid ≤20-char tags.
**Adversarial check performed:** Confirmed the forward fix (#7) is deployed but only affects NEW listings, so the backfill is still needed.

---

## 2. Full-system coverage — deeper checklist

**A. Per-agent prompt & reasoning audit** → `audit_data/agent_prompt_inventory.md`.
Registry (20) vs. live path documented; abstain paths verified (TrendResearchAgent
hard-abort ✓; IntelligenceAgent silent-empty ✗ → #10); schema enforcement gaps noted
(opportunities shape unchecked); zero-QA-coverage agents listed. Findings #10, #15.

**B. Full-catalog unit economics** → `audit_data/catalog_unit_economics.csv` (45/45).
Total est. cost **$11.85**, revenue **$0.00**, net **−$11.85**; cross-validated vs
autonomy ledger $11.71 (1.2%). Per-listing cost $0.21–$0.37. **Break-even 0.02–0.07
units** — a single sale covers cost 14–50×, so margins are NOT the problem.
*Sensitivity:* +20% on image/LLM pricing raises per-listing cost by ~$0.02–0.03
(image is $0.04→$0.048); break-even still < 0.1 units — margin is insensitive to
provider price. The binding variable is conversion (currently 0). Findings #7, #13, #16.

**C. Full log-history failure mining** (all 1912 log rows, 2026-07-07→07-21; note:
only WARNING+ persist to DB after the #14 handler deployed, so pre-deploy stdout
ERRORs are lost — a coverage gap, §4). Categorized: 402 halt (13), delivery-gen
blocks (71, via task table not logs), EtsyMarket 403 (live, ongoing), QA-repair
warnings (8, benign). **"Should have been caught sooner":** the EtsyMarket 403
header bug and the 60% block rate both predate this audit and had no dashboard/alert
surface — root cause: no one reads stdout, and the dashboard surfaces spend, not
block-rate or market-lookup health. Findings #2, #5, #6.

**D. Fault-injection / adversarial resilience** (read/dry-run):
- Etsy timeout mid-create: readback+rollback deletes unbacked drafts; `record_created_listing`
  guards resume double-listing. BUT #12 backoff retries 5xx on POST → low-prob double-create (#11).
- Malformed pytrends: TrendResearchAgent hard-aborts (no LLM-guess) — verified ✓.
- QA agent malformed JSON: bounded loop (`range(1, CONTENT_QA_MAX_ATTEMPTS+1)`), no infinite loop ✓.
- Concurrency: AutonomyWorker is a single thread + single loop; resume-scan bounded by
  PIPELINE_RESUME_MAX; race risk low. Finding #11.

**E. Competitive & market benchmarking** (live, 2026-07-21): coloring best-sellers are
bundles (50–3000 pages), not single pages → #16. **3 underserved fits visible in
current data, buildable now:** (1) multi-page coloring BUNDLES (pipeline already does
PDFs); (2) themed student/teacher PLANNER bundles (back-to-school is in-window; 2 of
the 4 pin attempts were back-to-school); (3) seasonal single-theme PDF packs. Exact
competitor prices/review counts UNVERIFIED (Etsy 403s automated fetch — §4).

**F. Security & secrets** (E8): no committed secrets in full git history; `.env`
untracked+gitignored; `DEBUG=False`, `ENV=production`; `FACTORY_API_KEY` set;
**no unauthenticated paid-action endpoint** (all mutating methods key-gated, only OAuth
callbacks open → #17). OAuth tokens live in the DB on the same volume as backups (#12).
Least-privilege: Etsy token scope UNVERIFIED (§4). Findings #12, #17.

**G. Legal/compliance/policy** (live policy, E9): AI disclosure required — mechanism
exists (SHOP_AI_DISCLOSURE); **velocity ~22/week vs ~5–10/week safe → #9**. who_made=i_did
is set; "Designed by a seller" categorization UNVERIFIED live. Visual-IP: the pipeline
now includes the #19 no-IP prompt, but content-QA already caught a trademark-ish title
("Black the School") — spot-check flagged for Maj's legal judgment (not cleared/condemned
here). Tax: revenue is $0, so no threshold crossed — revisit if sales start.

**H. Second-opinion cross-validation** (top findings re-derived via a 2nd path):
- Spend: autonomy_state ledger ($11.71) vs image-count costing ($11.85) — agree 1.2% ✓.
- Block rate: task `pipeline_status` (90) vs block-reason buckets (90) vs blocked-by-type
  (sum 89 typed +1) — consistent ✓.
- 0 sales: `sale_recorded`=0 (DB) AND `fee_estimate`=0 AND `catalog_unit_economics` revenue col all 0 ✓.
- Images-attached: bulk endpoint (0, artifact) vs per-listing readback (2–4) — the readback
  wins; prior "0 images" retraction re-confirmed ✓.
- Pinterest reality: config (sandbox on) + marketing_posts + public fetch (SPA 200, inconclusive)
  → confidence held at Medium, NOT upgraded on the weak signal ✓.
No top finding weakened under re-check except Pinterest reach (kept Medium, not High).

---

## 3. "Ready to run on its own" gate — numeric exit criteria

- [ ] **#1 Factory running:** `grep -c 402` in the last 3 h of logs = **0**, AND ≥ 1 new
  `task_completed` event in the last 2 h. (Requires OpenRouter balance > cost of ~24 cycles/day.)
- [ ] **#2 Block rate:** `BLOCKED_NO_PRODUCT / total` over the last 30 tasks **< 20%**
  (currently 60.8%); PDF block rate **< 25%** (currently 75%).
- [ ] **#6 Market data live:** a canary EtsyMarketService lookup returns
  `competition_count > 0` (currently 403 100%).
- [ ] **#3 Gate healthy:** with sonnet + live market data, `concept_scored.passed=true`
  rate **≥ 30%/day** before `PRODUCT_SCORE_ENFORCE=true` (currently 0%).
- [ ] **#4 Real traffic:** ≥ 1 `pinterest:success` whose pin id resolves via production
  `GET /v5/pins/{id}`, AND Etsy shop views rising over 7 days (currently max 2/listing).
- [ ] **#5 Alerting:** a forced cycle exception produces a Discord alert within one cycle.
- [ ] **#12 Backups:** a dated backup object exists in an external S3 bucket today.
- [ ] **#9 Velocity:** publish rate ≤ 10 listings/week.

Until #1, #2, and #6 are green, "unattended" means "manufacturing blocked/invisible
inventory at a loss."

---

## 4. Explicitly NOT verified (do not mistake for "checked and fine")

1. **Exact competitor prices/review counts/listing ages** — Etsy returns 403 to
   automated fetch (WebFetch) and market pages don't expose prices in search snippets.
   Needs a manual browse or an authenticated/headful scrape. (§2E direction is grounded;
   the numbers are not.)
2. **Whether the 4 Pinterest "successes" are truly sandbox** — inferred from
   `PINTEREST_SANDBOX=true` + SPA-200; NOT confirmed by an authenticated production
   `GET /v5/pins/{id}` (would need the production token, and the app is sandbox-pinned).
3. **Full pre-#14 ERROR history** — before the DB-log handler deployed, ERRORs went to
   stdout only and are lost on container recycle; the 27 ERROR rows are the *persisted*
   subset, not the true historical total.
4. **True total blocked-task spend** — `image_assets` are auto-pruned, so the $1.72
   waste figure is a floor; the real total (incl. deleted PDF-page images) is higher and
   not reconstructable from surviving rows.
5. **Etsy OAuth token scope (least-privilege)** — not enumerated; needs a token-introspection
   call or the Etsy app's granted-scopes page.
6. **"Designed by a seller" attribution + AI disclosure rendering** on live listings —
   who_made=i_did is set in code; the exact live listing attribution + disclosure string
   were not read back per-listing this pass.
7. **Whether Maj was actually paged for the 9 h halt** — no alert marker exists, but a
   Discord/webhook delivery outside the DB can't be confirmed from here.
