"""
Backfill Pinterest posts for the whole past catalog.

Pins EVERY published product that has never had a successful Pinterest post,
using assets already on disk (no image generation). Idempotent — re-runs skip
what's already pinned.

MUST run where the production DB + image volume + OAuth token live (i.e. inside
the Railway container, or with DATABASE_PATH/IMAGE_STORAGE_ROOT pointed at them).
Easiest path: trigger the admin endpoint instead — see
POST /admin/pinterest-backfill.

Usage:
  python scripts/backfill_pinterest_posts.py                 # dry run (prints plan)
  python scripts/backfill_pinterest_posts.py --apply         # post (default limit 50)
  python scripts/backfill_pinterest_posts.py --apply --limit 20 --sleep 4
  python scripts/backfill_pinterest_posts.py --apply --no-rewrite   # no caption LLM call
"""
import sys

sys.path.insert(0, ".")


def _arg(name, default=None, cast=str):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return cast(sys.argv[i + 1])
    return default


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    limit = _arg("--limit", 50, int)
    sleep = _arg("--sleep", 3.0, float)
    rewrite = "--no-rewrite" not in sys.argv

    from app.services.pinterest_backfill_service import PinterestBackfillService
    rep = PinterestBackfillService().run(apply=apply, limit=limit, sleep_seconds=sleep, rewrite_caption=rewrite)

    print(f"\n{'APPLIED' if apply else 'DRY RUN'} — Pinterest backfill")
    print(f"  candidates (never pinned): {rep['total_candidates']}")
    print(f"  in this run (limit {limit}): {rep['to_post']}")
    if rep.get("error"):
        print(f"  ERROR: {rep['error']}")
        sys.exit(1)
    if not apply:
        for c in rep.get("dry_run", []):
            print(f"    - {c['task_id']}  {c['title']}")
        print(f"\n  Re-run with --apply to pin these ({rep['to_post']} posts, ~{sleep}s apart).")
    else:
        print(f"  posted OK: {rep['posted']}/{rep['to_post']}")
        for r in rep["results"]:
            flag = "OK " if r["success"] else "ERR"
            print(f"    [{flag}] {r['title']}  {r.get('url') or r.get('error')}")
