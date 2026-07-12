"""
Backfill occasion metadata (STEP 105 1-7).

The seasonal-listing lifecycle (104 1-4) only deactivates listings whose task
carries metadata_["occasion"] — a stamp that only autonomy tasks created AFTER
104-B have. Every pre-104 listing (the Easter/Mother's Day/Thanksgiving ones the
audit flagged) is invisible to it. This one-off backfill runs occasion_for(title,
description) over every DONE product task and stamps the occasion, so the
existing weekly lifecycle tick handles the whole catalog forever.

Dry-run by default (prints the mapping); pass --apply to write.

Usage:
  python scripts/backfill_occasion_metadata.py            # dry run
  python scripts/backfill_occasion_metadata.py --apply    # write stamps
"""
import sys

sys.path.insert(0, ".")

from app.db.database import SessionLocal
from app.models.task import Task
from app.schemas.enums import TaskStatus
from app.core.seasonality import occasion_for, occasion_in_window
from app.core.product_formats import PRODUCT_FORMATS


def run(apply: bool = False) -> dict:
    db = SessionLocal()
    stamped, skipped, already = [], 0, 0
    try:
        tasks = db.query(Task).filter(Task.status == TaskStatus.DONE.value).all()
        for t in tasks:
            out = t.output_data or {}
            title = out.get("title") or (t.metadata_ or {}).get("product_name") or t.title or ""
            desc = out.get("description") or ""
            # only product tasks (those tied to a real product_format)
            fmt = (t.metadata_ or {}).get("product_format") or out.get("product_format")
            if fmt and fmt not in PRODUCT_FORMATS:
                continue
            meta = t.metadata_ or {}
            if meta.get("occasion"):
                already += 1
                continue
            occ = occasion_for(title, desc)
            if not occ:
                skipped += 1
                continue
            in_window = occasion_in_window(occ)
            stamped.append({"task_id": t.id, "title": title[:70], "occasion": occ,
                            "in_window_now": in_window})
            if apply:
                t.metadata_ = {**meta, "occasion": occ}
        if apply and stamped:
            db.commit()
    finally:
        db.close()
    return {"stamped": stamped, "already_stamped": already, "no_occasion": skipped, "applied": apply}


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    rep = run(apply=apply)
    print(f"\n{'APPLIED' if apply else 'DRY RUN'} — occasion backfill")
    print(f"  already stamped : {rep['already_stamped']}")
    print(f"  no occasion     : {rep['no_occasion']}")
    print(f"  newly stamped   : {len(rep['stamped'])}")
    for s in rep["stamped"]:
        flag = "IN-WINDOW" if s["in_window_now"] else "OUT-OF-WINDOW (lifecycle will deactivate)"
        print(f"    [{s['occasion']:<16}] {flag:<45} {s['title']}")
    if not apply and rep["stamped"]:
        print("\nRe-run with --apply to write these stamps.")
