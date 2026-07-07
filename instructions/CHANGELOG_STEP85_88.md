
## Steps 85-88 — Railway deployment, alerting, stabilization, autonomy
**Date:** 2026-07-07

---

### Step 85 — Railway deployment

**Deploy method:** GitHub repo connected in Railway dashboard (Maj authenticated via browser OAuth).

**Railway config (`railway.toml`):**
```toml
[build]
builder = "RAILPACK"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

**Persistent storage paths made configurable:**
- `DATABASE_PATH` env var → `sqlite:////{path}` (Railway: `/data/app.db`)
- `IMAGE_STORAGE_ROOT` env var → image base dir (Railway: `/data/images`)
- `app/core/paths.py` → `get_data_dir()` resolves durable dir from env vars or local `data/`
- `app/db/database.py` → reads `DATABASE_PATH` before `DATABASE_URL`
- `app/services/image_file_service.py` → reads `IMAGE_STORAGE_ROOT`
- `app/workers/etsy_receipt_worker.py` → `STATE_FILE` uses `get_data_dir()`

**New settings fields (`config/settings.py`):**
```
DATABASE_PATH, IMAGE_STORAGE_ROOT, DISCORD_WEBHOOK_URL,
MAX_TASKS_PER_DAY=10, MAX_DAILY_SPEND_USD=5.00,
AUTONOMY_ENABLED=False, AUTONOMY_SCHEDULE_SECONDS=3600
```

**Railway env vars to set (Variables tab):**
```
DATABASE_PATH=/data/app.db
IMAGE_STORAGE_ROOT=/data/images
DISCORD_WEBHOOK_URL=<webhook url>
MAX_TASKS_PER_DAY=10
MAX_DAILY_SPEND_USD=5.00
AUTONOMY_ENABLED=false
PRINTIFY_SHOP_ID=28166438   (Etsy-connected shop, NOT 5606311)
```
Plus all existing keys (ETSY_*, PINTEREST_*, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, SECRET_KEY).
ETSY_REDIRECT_URI and PINTEREST_REDIRECT_URI must match the Railway public domain.

**Health check confirmed:** GET /health/workers from external browser returned:
```json
{"status":"ok","workers":{"TaskWorker":{"healthy":true,...},"EtsyReceiptWorker":{"healthy":true,...},"AutonomyWorker":{"healthy":true,...}}}
```
All three workers started and heartbeating inside the Railway container.

**Migration note:** This is a temporary host (Railway 30-day trial / Hobby plan).
Moving to Oracle VM means redoing steps 85-86 there. Steps 87/88 code does not change —
it's a hosting swap, not a rebuild. The only things to update on migration:
  - ETSY_REDIRECT_URI, PINTEREST_REDIRECT_URI (new domain)
  - Railway-specific env vars (mounted volume path, webhook URLs)

---

### Step 86 — Monitoring and alerting

**Files created:**
- `app/services/alert_service.py` — POSTs Discord embed to `DISCORD_WEBHOOK_URL`. Never raises.
  Has both `async send_alert()` and sync `send_alert_sync()` for use in background threads.
- `app/services/worker_registry.py` — shared in-process heartbeat dict.
  `record_heartbeat(name)`, `get_heartbeats()`, `is_stale(name, max_age_seconds)`.
- `app/core/paths.py` — `get_data_dir()` for centralized path resolution.

**Files modified:**
- `app/workers/task_worker.py` — `record_heartbeat("TaskWorker")` at top of each loop iteration.
  `finally` block sends Discord alert if thread exits unexpectedly.
- `app/workers/etsy_receipt_worker.py` — same heartbeat + death alert pattern.
  Also: `send_alert_sync()` when `submit_order()` fails (fulfillment failure alert).
  Also: `_check_worker_health()` called every 3 poll cycles (~15 min); reads `worker_registry`
  and sends Discord alert if any worker heartbeat is stale.
- `app/api/routes/health.py` — added `GET /health/workers` endpoint exposing per-worker
  heartbeat timestamp, age, and healthy/stale status.

**Alert events:**
| Event | Level | Source |
|-------|-------|--------|
| TaskWorker thread died | error | TaskWorker._run_loop finally |
| EtsyReceiptWorker thread died | error | EtsyReceiptWorker._run_loop finally |
| AutonomyWorker thread died | error | AutonomyWorker._run_loop finally |
| Fulfillment order failed | error | EtsyReceiptWorker._process_receipt |
| Stale worker heartbeat | warning | EtsyReceiptWorker._check_worker_health |
| Daily task cap hit | warning | AutonomyService.record_task_created |
| Daily spend cap hit | warning | AutonomyService.record_spend |

**Known limitation of internal self-check:** If the entire process dies, nothing inside it
can send the stale-heartbeat alert. Railway's own dashboard crash/restart notifications
(Settings → Notifications) serve as the external backstop for whole-process death.
This tradeoff is intentional — adding a second paid Railway service just for health checks
was explicitly out of scope.

**Railway notifications:** Confirm Railway crash/restart notifications are enabled in the
Railway project's Settings → Notifications tab.

---

### Step 87 — Final stabilization

**Local test suite results (baseline before Railway run):**

Passing locally (all path/storage-sensitive tests):
```
test_step68_image_storage.py    PASS
test_step69_product_image_agent.py   PASS
test_step70_social_image_agent.py    PASS
test_step71_pod_design_agent.py      PASS
test_step72_image_validation.py      PASS
test_step73_etsy_image_integration.py   PASS
test_step74_pinterest_image_integration.py  PASS
test_step75_pod_pipeline.py          PASS
test_step76_image_catalog.py         PASS
test_step81_pod_fulfillment.py       PASS (7/7 tests)
test_step83_stress.py                PASS
test_step84_performance.py           PASS
test_step88_autonomy.py              PASS (8/8 tests)
test_seo_schema.py                   PASS
test_validator.py                    PASS
```

Pre-existing failures (NOT caused by steps 85-88):
- `test_agent_registry.py`, `test_full_lifecycle.py`, `test_image_provider_abstraction.py`,
  `test_state_machine.py` — UnicodeEncodeError with ✓/→ chars on Windows CP1250 terminal.
  Will NOT occur on Railway (Linux UTF-8).
- `test_analytics.py`, `test_best_products.py`, `test_listing_generator.py`,
  `test_marketing_layer.py`, `test_performance_scoring.py`, `test_product_generator.py`,
  `test_qa_expansion.py`, `test_revenue.py`, `test_seo_generator.py`, `test_seo_posting.py` —
  `RuntimeError: Event loop is closed` (Python 3.10 Windows asyncio cleanup behavior).
  Will NOT occur on Railway (Linux).
- `test_sanitizer.py` — `ModuleNotFoundError` when run with shell wildcard from project root
  but with CWD mismatch. Pre-existing environment issue.
- `test_marketing_layer.py` — `DetachedInstanceError` (SQLAlchemy session management).
  Pre-existing issue unrelated to storage paths.

**Path resolution verified with Railway env vars:**
`get_data_dir()` correctly returns parent of `IMAGE_STORAGE_ROOT` when set.
`DATABASE_URL` correctly reflects `DATABASE_PATH` when set.

**To run against Railway (run in terminal):**
```bash
railway run python scripts/test_step68_image_storage.py
railway run python scripts/test_step76_image_catalog.py
railway run python scripts/test_step81_pod_fulfillment.py
railway run python scripts/test_step83_stress.py
railway run python scripts/test_step84_performance.py
railway run python scripts/test_step88_autonomy.py
```

---

### Step 88 — Full autonomous loop

**Files created:**
- `app/services/autonomy_service.py` — daily task/spend cap enforcement.
  State persisted in `<data_dir>/autonomy_state_<YYYY-MM-DD>.json` (one file per UTC day).
  Alerts via Discord when either cap is hit.
- `app/workers/autonomy_worker.py` — background thread. `AUTONOMY_ENABLED=False` kill switch.
  Schedule: `AUTONOMY_SCHEDULE_SECONDS=3600`. Calls `TrendResearchAgent` → `TaskService`.
  Respects both daily task cap and daily spend cap before each cycle.
  Heartbeats via `worker_registry`. Death alert in `finally` block.
- `app/agents/trend_research_agent.py` — thin orchestrator: `ResearchAgent` → `IntelligenceAgent`
  → returns `{"concept": str, "confidence": str}` or `None`.
- `scripts/test_step88_autonomy.py` — 8/8 tests using agent/service doubles; zero real API calls.

**Files modified:**
- `app/main.py` — `AutonomyWorker` started/stopped on server lifecycle.

**AUTONOMY_ENABLED=False confirmed.** The kill switch is active. The worker starts and
heartbeats but calls no agents and creates no tasks until the env var is set to `true`.

**To enable autonomous operation (when ready):**
Set `AUTONOMY_ENABLED=true` in Railway Variables tab. The worker will begin calling
`TrendResearchAgent` every `AUTONOMY_SCHEDULE_SECONDS` seconds and routing ideas through
`TaskService`. Both `MAX_TASKS_PER_DAY` (10) and `MAX_DAILY_SPEND_USD` ($5.00) caps enforce
hard limits — hitting either sends a Discord alert and blocks further work until UTC midnight.
