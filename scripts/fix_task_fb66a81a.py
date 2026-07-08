"""
One-off — retroactively annotate task fb66a81a with what actually happened.

Investigation (2026-07-08, via railway ssh + real Etsy API calls) found the
originally-reported symptom ("2 photos, no downloadable file") was WRONG:
Etsy's own getAllListingFiles endpoint confirmed a real file (design.png,
550KB) was attached from the very first pipeline run. The REAL bug: the
listing's `state` was stuck at "edit" (draft, not live/purchasable) because
EtsyImageService.publish_listing()'s PATCH call returned HTTP 200 without
the state transition actually taking effect (a propagation-lag race right
after the preceding image/file uploads) -- and the pipeline only checked
the HTTP status code, never the response body's real `state`, so it
recorded attach_publish as fully successful when the listing was still a
draft.

During this same investigation, a diagnostic re-invocation of the identical
PATCH call (needed to inspect the real response body) had the side effect
of ACTUALLY completing the state transition to "active" -- the listing is
now genuinely live, with real photos and a real file, but that happened as
an unintended byproduct of debugging, not as a verified outcome of a gate
that has since been fixed (step 92: publish_listing() now checks the real
state and retries; a listing that still isn't active is deleted and the
task is blocked).

Does NOT touch task.status (DONE) or delete anything -- only merges keys
into output_data, same pattern as the 97f0e7a0 fix.

Run via railway ssh (chunked base64 transfer -- see MIGRATION_NOTES.md):
  railway ssh -- "... python3 scripts/fix_task_fb66a81a.py"          # dry run
  railway ssh -- "... python3 scripts/fix_task_fb66a81a.py --yes"    # apply
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal
from app.models.task import Task

TASK_ID = "fb66a81a-b7be-48db-843c-5aed7a87383e"
PIPELINE_NOTE = (
    "Original pipeline run (2026-07-08T09:36:52Z) reported attach_publish as "
    "fully successful (listing_id=4534427807), but the listing's real state "
    "remained 'edit' (draft, not live/purchasable) at that time -- "
    "publish_listing() only checked the HTTP status of the publish PATCH "
    "call, never the response body's actual 'state' field, so a silent "
    "no-op (200 OK, state unchanged) was recorded as success. Both listing "
    "photos and the digital file (design.png, 550KB) WERE genuinely "
    "attached from the start -- confirmed via Etsy's own getAllListingFiles "
    "endpoint (count=1) -- so the originally-reported symptom ('photos but "
    "no file') does not match what actually happened. The listing was "
    "manually transitioned to 'active' during the 2026-07-08 investigation "
    "of this bug, as an unintended side effect of a diagnostic re-invocation "
    "of the same publish call -- not as a verified outcome of a passing "
    "gate. The underlying gap (no readback confirming the digital file's "
    "presence, and no verification that publish actually changed state) is "
    "fixed in step 92."
)

db = SessionLocal()
try:
    task = db.query(Task).filter(Task.id == TASK_ID).first()
    if not task:
        print(f"Task {TASK_ID} not found. Nothing to do.")
        sys.exit(1)

    current = dict(task.output_data or {})
    if current.get("pipeline_status") == "PUBLISH_NOT_VERIFIED_AT_COMPLETION":
        print(f"Task {TASK_ID} is already annotated. Nothing to do.")
        sys.exit(0)

    updated = dict(current)
    updated["pipeline_status"] = "PUBLISH_NOT_VERIFIED_AT_COMPLETION"
    updated["pipeline_note"] = PIPELINE_NOTE
    updated["etsy_listing_id"] = "4534427807"

    print(f"Task {TASK_ID} (status={task.status}, type={task.type})")
    print("Current output_data keys:", list(current.keys()))
    print("\nWill add to output_data:")
    print(f"  pipeline_status: {updated['pipeline_status']}")
    print(f"  etsy_listing_id: {updated['etsy_listing_id']}")
    print(f"  pipeline_note: {updated['pipeline_note'][:150]}...")
    print("\ntask.status is NOT changed (stays DONE) — only output_data is merged.")

    if "--yes" not in sys.argv:
        print("\nDry run only. Re-run with --yes to apply this change.")
        sys.exit(0)

    task.output_data = updated
    db.commit()
    print(f"\nApplied. Task {TASK_ID} output_data updated.")
finally:
    db.close()
