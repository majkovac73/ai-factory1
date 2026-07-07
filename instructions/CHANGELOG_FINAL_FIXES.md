
## Final fixes — DB pollution, idempotency hardening, alert debouncing
**Date:** 2026-07-07

---

### Part 1 — Diagnosis: pollution vs. real bug

**Finding (to be confirmed by running `railway run python scripts/diagnose_db_pollution.py`):**

The step 83 assertion failure was almost certainly test data pollution, not a real
idempotency bug. Both `test_step81_pod_fulfillment.py` and `test_step83_stress.py` set
`DATABASE_URL` to a temp DB before importing app modules, but on Railway
`DATABASE_PATH=/data/app.db` is set as an env var and takes precedence over `DATABASE_URL`
in `app/db/database.py`. This meant every `railway run python scripts/test_step8*.py`
wrote directly into `/data/app.db` instead of a throwaway temp file.

Run `scripts/diagnose_db_pollution.py` to print every FulfillmentRecord row and confirm
which rows are STRESS-RECEIPT-* test artifacts vs. real Etsy receipt rows.

---

### Part 2 — Test data cleanup and isolation fix

**One-off cleanup (run once on Railway):**
```bash
railway run python scripts/cleanup_test_data.py
```
Deletes:
- PODProduct rows with etsy_listing_id in (999000111, 999000222) — from step 81 tests
- FulfillmentRecord rows with etsy_receipt_id starting with STRESS-RECEIPT-* — from step 83
- FulfillmentRecord rows tied to the above PODProduct rows

**Root cause fix (`test_step81_pod_fulfillment.py` and `test_step83_stress.py`):**
Added `os.environ.pop("DATABASE_PATH", None)` immediately before the `DATABASE_URL` override
in both scripts. This clears Railway's `DATABASE_PATH` env var so the test's `DATABASE_URL`
(pointing to the temp file) is no longer overridden.

Why `DATABASE_PATH` takes precedence: `app/db/database.py` checks `DATABASE_PATH` before
`DATABASE_URL` so that Railway's `/data/app.db` mount is respected. Tests need to explicitly
unset it to prevent the production DB from being used.

---

### Part 3 — IntegrityError handling in submit_order()

**File:** `app/services/pod_fulfillment_service.py`

Wrapped the `db.add(record); db.commit()` block in `try/except IntegrityError`. On catching:
1. `db.rollback()` — cleans the session immediately
2. Query for the existing FulfillmentRecord at the same (receipt_id, transaction_id)
3. Return it — caller sees a valid record, no exception propagates
4. No AlertService call — this is expected concurrent behavior, not an actionable failure

**Test added (`test_step83_stress.py` test [6]):**
- Calls `submit_order()` twice with the same (receipt_id, transaction_id)
- Asserts second call returns same record without raising
- Asserts DB has exactly 1 row for that key
- Asserts a third call with a DIFFERENT receipt_id succeeds (proving session is clean)

**Race test update:** The concurrent test [3] previously asserted race_errors contained only
IntegrityErrors. After this fix, IntegrityErrors are swallowed inside `submit_order()`, so
race_errors is expected to be empty — updated assertion accordingly.

---

### Part 4 — Alert debouncing

**File:** `app/services/alert_service.py`

Added module-level `_last_sent: Dict[str, float]` and `DEBOUNCE_SECONDS = 60`.

Before sending, `send_alert()` checks if the same title was sent within the last 60 seconds.
If so, logs at DEBUG level and returns False without hitting Discord. The timestamp is updated
only on a successful send.

Key design decisions:
- **Module-level** (not instance-level): `AlertService()` is typically instantiated fresh per
  call from background threads. Instance-level debouncing would be useless.
- **Title as key**: coarse enough to group "Fulfillment order failed" alerts without blocking
  completely distinct alert categories from each other.
- **60s cooldown**: enough to absorb a concurrent burst; short enough that a persistent
  problem generates a follow-up alert within a minute.

---

### Part 5 — Re-run confirmation

**Local test results after all fixes:**
```
test_step81_pod_fulfillment.py  PASS (7/7)
test_step83_stress.py           PASS (includes new test [6])
```

**Railway re-run commands (run after cleanup):**
```bash
railway run python scripts/diagnose_db_pollution.py   # confirm what's there first
railway run python scripts/cleanup_test_data.py        # clean up, confirm
railway run python scripts/test_step83_stress.py       # should pass cleanly now
railway run python scripts/test_step81_pod_fulfillment.py
railway run python scripts/test_step88_autonomy.py
```

Expected: exactly 0 FulfillmentRecords remaining after cleanup; stress test creates exactly
12 (10 race + 2 clean-session), all with unique composite keys; no IntegrityError-sourced
Discord alerts; no alert storms.
