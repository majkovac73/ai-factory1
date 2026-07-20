# Audit 2026-07-20 — Maj's manual actions (config / non-code)

All **code** items from `AUDIT_AND_OPTIMIZATION_PLAN_2026-07-20.md` are implemented,
tested, and on branch `audit-step102-fixes`. The items below cannot be done from
code — they are Railway env vars or Etsy/Pinterest dashboard actions. Do them in
this order; #1 and #2 are the two that convert "a working factory" into "a factory
that can make a sale".

---

## P0

### #1(a) — Land Pinterest Standard access (the #1 traffic blocker)
- In Railway, `PINTEREST_APP_SECRET` is currently a **101-char value (an access
  token, not the ~64-char app secret)** for app `1589935`, which 401s the OAuth
  token exchange. **Set the real App-secret for app `1589935`.**
- Verify: `railway ssh` then
  `python -c "from config import settings as s; print(len(s.PINTEREST_APP_SECRET))"`
  → expect ~64, not 101.
- Complete the OAuth demo, submit app `1589935` for **Standard** access.
- **After Standard is granted:** the pipeline auto-resumes pin work (the new
  `can_publish()` probe auto-detects the first successful pin). To force-enable
  immediately, set `PINTEREST_CAN_PUBLISH=true`. To hard-disable, set it `false`.

### #2 — Put concept generation on a strong model
- Set `CONCEPT_MODEL=anthropic/claude-sonnet-5` in Railway. This raises concept
  quality AND makes the two score judges genuinely independent (concept_model vs
  DEFAULT_MODEL). Keep `DEFAULT_MODEL` cheap for downstream work.
- Watch new `concept_scored` events: target median ≥ 80 with a non-zero pass rate.

### #3 — Enable the quality gate ONLY after #2 (correct ordering)
- Keep `PRODUCT_SCORE_ENFORCE=false` until `concept_scored` shows a healthy pass
  rate (~≥30% of scored concepts pass the floors across a day) with the strong
  `CONCEPT_MODEL`. **Then** set `PRODUCT_SCORE_ENFORCE=true`.
- Guardrail now in place: if enforce is on and **3 consecutive cycles** produce
  zero passers, you get a Discord alert (tunable via `PRODUCT_ENFORCE_ZERO_STREAK_ALERT`)
  so a too-tight gate can't silently halt the factory.

---

## P1 / P2 (config)

### #6 — Re-enable re-promotion (AFTER #1 lands)
- Once Pinterest posts in production, set `MARKETING_REFRESH_ENABLED=true`. Before
  Pinterest works, refreshing only to Tumblr has limited value, so sequence it
  after #1.

### #13 — Off-box backups (SPOF fix)
- `boto3` is already in `requirements.txt` (installed in the container). Set
  `BACKUP_S3_BUCKET` (+ `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_ACCESS_KEY_ID`,
  `BACKUP_S3_SECRET_ACCESS_KEY`) for any S3-compatible bucket (R2 / B2 / Railway).
  Then `_upload_offsite` runs. Verify a backup object appears in the bucket today
  and rehearse one restore. **Do this before the Oracle Cloud VM move.**

### #18 — Pinterest App-ID hygiene
- Standardize on app **`1589935`** everywhere (secret, redirect URI, review).
  Docs updated to drop the superseded `1587865`.
- **Unset `PINTEREST_SANDBOX`** in Railway (OAuth exchange is production-pinned now;
  the flag is a stale footgun for the non-OAuth pin path).

---

## P3 (manual, Etsy dashboard)

### #16 — Shop trust signals
- Write a shop **announcement**, **About/Story**, a clear **digital-download
  policy**, and a simple **banner**. Consider seeding a few honest early sales/reviews
  via legitimate promotion. Not a code task.

### #17 / #19 — Spot checks (manual complement to the code changes)
- #17: spot-check ~5 real competitor listings per top format for price/review
  reality (WebFetch is 403'd by Etsy, so this needs a manual look). The concept
  generator now gets margin-ranked guidance that de-prioritizes low-margin
  coloring pages.
- #19: periodically eyeball generated art for derivative IP. The design prompts
  now instruct "no recognizable characters, logos, or copyrighted artwork", but
  that's a mitigation, not a guarantee.

---

## New env knobs introduced (all have safe defaults — no action required)

| Var | Default | Purpose |
|-----|---------|---------|
| `PINTEREST_CAN_PUBLISH` | unset (auto-detect) | #1c/#5 force pin publish on/off |
| `PRODUCT_ENFORCE_ZERO_STREAK_ALERT` | 3 | #3 consecutive zero-passer alert threshold |
| `LISTING_IMAGE_SIZE` | 2000 | #8 listing mockup resolution (px) |
| `LISTING_HERO_W` / `LISTING_HERO_H` | 2000 / 1600 | #8 landscape hero dimensions |
| `LEARNING_MIN_VIEWS_FOR_SIGNAL` | 50 | #9 view floor below which internal bias is suppressed |
| `LOW_MARGIN_DEPRIORITIZE_FORMATS` | coloring_page, phone_wallpaper | #17 formats de-prioritized in concept gen |

## New DB signals now queryable (verification)
- `analytics_events` gains `cost_incurred` (per-task cost) and `trend_signal`
  (per-cycle trend coverage). `GET /dashboard/pnl-by-listing` shows per-listing P&L.
- `task_steps` + `agent_executions` now populate per pipeline run (#15).
- `logs` table now captures WARNING/ERROR (#14): `LogService().error_summary()`.
- `/dashboard/production` now reports `blocked_tasks_24h` + top reasons (#11).
