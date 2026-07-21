# DEEP AUDIT V2 — Evidence Log (2026-07-21)

Every claim in `DEEP_AUDIT_V2_2026-07-21.md` traces to a command below. All
production data pulled via `railway ssh` (linked service `kind-liberation`,
project `loving-imagination`, env `production`) against the live `/data/app.db`
(48,975,872 bytes, WAL active) and the live Etsy API. Diagnostic scripts kept in
`scripts/audit/deep_*.py`.

Runner pattern: `B64=$(base64 -w0 scripts/audit/<x>.py); railway ssh "cd /app && echo $B64 | base64 -d > /tmp/x.py && python3 /tmp/x.py"`.

---

## E1 — Access validation
`railway status` → project loving-imagination / service kind-liberation / env production / volume `/data` 0.6GB of 4.9GB.
`railway ssh "ls -la /data"` → `/data/app.db` 48,975,872 bytes (mtime 2026-07-20 21:53), `app.db-wal` 3,868,712 bytes (mtime 2026-07-21 11:19), `autonomy_state_*.json` for 2026-07-07 … 2026-07-21 (15 files). Python 3.13.14 in container.

## E2 — Full DB census (`deep_db_census.py`)
Tables + row counts: tasks=148, analytics_events=879, logs=1912, marketing_posts=94, image_assets=256, agent_executions=1, task_steps=3, pod_products=0, fulfillment_records=0, etsy_tokens=1, pinterest_tokens=1, tumblr_tokens=1, memory=0.
- `tasks_status`: **DONE=148 (100%)** — no FAILED/BLOCKED status ever set.
- `tasks_pipeline_status`: **BLOCKED_NO_PRODUCT=90, COMPLETED=36, null=21, PUBLISH_NOT_VERIFIED_AT_COMPLETION=1**.
- `tasks_type`: pdf_planner_or_guide=56, coloring_page=50, single_print=20, sticker_sheet_design=6, seamless_pattern=3, phone_wallpaper=3, greeting_card_design=3, general=3, pod_apparel_design=2, seo_writing=1, digital_download=1.
- `analytics_event_type`: listing_stats=419, marketing_post_failed=149, task_completed=148, concept_scored=83, marketing_post_success=52, cost_incurred=14, trend_signal=14.
- `marketing_channel_status`: tumblr:success=48, pinterest:failed=41, pinterest:success=4, tumblr:failed=1.
- `logs_level`: INFO=1874, ERROR=27, WARNING=11. Range 2026-07-07 20:16 → 2026-07-21 10:57.
- `image_use_case`: listing=160, delivery=72, pinterest=24. `image_provider_model`: openrouter:seedream-4.5=130, pil composites=126.
- Revenue events: **sale_recorded=0, fee_estimate=0**. cost_incurred=14 ($0.116). concept_scored=83 (sum 5217). trend_signal=14. listing_stats=419 (sum value 58).

## E3 — Failure mining + spend (`deep_failure_mining.py`)
- **402 halt**: error logs contain 13× `TrendResearchAgent: research step failed: Error code: 402 - 'This request requires more credits, or fewer max_tokens'`. First 402 at **2026-07-21 01:57:03**; recurring hourly through 10:57 (last log line). 27 total ERROR rows, 13 are 402.
- `concept_scored_stats`: n=83, mean=**62.86**, min=55, max=71. `concept_passed`: **passed=0 → 83 rows (0% pass)**.
- `pinterest_success_detail`: 4 rows — external_ids 1132866481297496924/…497296 (2026-07-15, → etsy 4536543883) and …870034/…870190 (2026-07-20, → etsy 4537010013).
- `pinterest_fail_range`: 41 failures 2026-07-13 17:00 → 2026-07-20 18:12.
- `autonomy_state` totals: **total_spend_usd = $11.706**, total tasks_created (ledger) = 134, across 2026-07-07…07-21. Big days: 07-10 $2.686 (16 tasks), 07-12 $2.146, 07-13 $1.942, 07-14 $1.432. 07-21 only $0.116 / 1 task (halted).
- `warning_logs`: QA-repair warnings cluster 07-07…07-10; **EtsyMarketService 403 "Shared secret is required in x-api-key header"** on 07-21 (2 rows).
- `cost_by_use_case` (since ledger deployed): text_llm 14 events $0.116.
- `listing_stats` distribution: 368 obs @ 0 views, 44 @ 1 view, 7 @ 2 views → **max 2 views on any listing**.

## E4 — Block buckets + live config (`deep_config_and_blocks.py`)
- `block_buckets` (full 90): **"no verified PDF" = 40**, "no verified delivery asset" = 31, "marketing/deliverable mismatch" = 9, "content quality gate failed" = 8, "Pre-fix bad listing" = 1, "attach/publish failed" = 1. → **71/90 (79%) are delivery-asset generation failures.**
- `blocked_by_type`: pdf_planner_or_guide 42 blocked / 9 completed / 56 total (**75% block**); coloring_page 26/12/50 (52%); single_print 11/8/20 (55%); seamless_pattern 3/0/3 (100%); phone_wallpaper 3/0/3 (100%); pod_apparel_design 2/0/2 (100%).
- `alert_markers`: `enforce_zero_passer_streak.json`={streak:0}; `blocked_tasks_alert.json`={at:1784630483} (blocked-tasks alert HAS fired). **No `zero_production_alert.json`** present.
- EtsyMarketService source (in-container): `self._api_key = settings.ETSY_API_KEY` and `headers={"x-api-key": self._api_key}` → keystring only (bug).
- `completed_with_listing` = 36.

## E5 — Live config (`railway variables --kv`)
`AUTONOMY_ENABLED=true, AUTO_PUBLISH_LISTINGS=true, CONCEPT_MODEL=anthropic/claude-sonnet-5, MAX_DAILY_SPEND_USD=5.00, MAX_TASKS_PER_DAY=10, PINTEREST_APP_ID=1589935, PINTEREST_SANDBOX=true, FACTORY_API_KEY=<set>, DEBUG=False, ENV=production, LOG_LEVEL=info`. (PRODUCT_SCORE_ENFORCE, MARKETING_REFRESH_ENABLED, BACKUP_S3_BUCKET, PINTEREST_CAN_PUBLISH, DEFAULT_MODEL not set → defaults: enforce False, refresh False, no offsite backup, gpt-4o-mini default.)

## E6 — Unit economics (`deep_unit_economics.py` → `catalog_unit_economics.csv`)
45 active listings. **Total est. generation cost $11.85, revenue $0.00, net −$11.85.** (Cross-validates E3 autonomy ledger $11.71 within 1.2%.) Per-listing est. cost $0.21–$0.37; break-even 0.02–0.07 units (≤1 sale covers cost). Prices €3.50–€19.99; a cluster of coloring_page listings priced €15.00 (band is €2–4.50 → early mispricing). Tags: many 13, several 3/5/6/7/8 (under-filled). `images` column = 0 for all (bulk-endpoint artifact — see E7).

## E7 — Authoritative verification (`deep_waste_and_verify.py`)
- **Per-listing image readback** (GET /listings/{id}/images) on 6 listings: 2–4 real images each, **all 1024×1024** → images ARE attached (CSV `images=0` is the bulk artifact, retraction from prior audit re-confirmed); #8 2000px fix NOT yet on live catalog.
- **Blocked-task image waste**: billable(openrouter) images total=130, on blocked tasks=**43 ($1.72)**, on completed=42. (Floor only — image_assets are auto-pruned, so historical waste is higher.)
- **Pinterest**: `PINTEREST_SANDBOX=true`. Public fetch of pin 1132866481297496924 → HTTP 200 but Pinterest is a SPA (200 shell for any URL) → inconclusive; sandbox at creation ⇒ pins most likely sandbox-only (no real reach).

## E8 — Secrets (2F, local git)
`git log --all -p` grep for key/token patterns → **no committed secrets**. `.env` never tracked; `.gitignore` covers `.env`/`.env.*`. `railway variables`: `FACTORY_API_KEY` set, `DEBUG=False`. `app/api/auth.py`: ALL POST/PUT/PATCH/DELETE require X-Factory-Key except 3 OAuth callbacks → **no unauthenticated paid-action endpoint**.

## E9 — Live policy + market (2E/2G, WebSearch)
- Etsy AI policy (current): description must mention "AI"; attribution "Designed by a seller"; **≥50 near-identical AI listings/day trips spam filters; 5–10/week is safe**. Shop published 45 in 14 days (~22/week). Sources: inkfluenceai.com, ngini.com, etsy.com/legal/creativity.
- Coloring-page market: best-sellers are **bundles (50–3000 pages)**, not single pages. Exact competitor prices UNVERIFIED (Etsy 403s automated fetch).
