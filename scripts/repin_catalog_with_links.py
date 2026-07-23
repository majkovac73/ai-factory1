"""
Re-pin the published catalog WITH working listing links, and remove the old
linkless pins (dead ends that drive zero traffic).

Every Pinterest pin created before the _stage_pinterest link fix has link=None.
This script:
  1. reads back every pin we recorded (marketing_posts) and finds the LINKLESS ones,
  2. DELETEs those dead pins from Pinterest and downgrades their marketing_post row
     (so the product becomes eligible to be re-pinned),
  3. re-pins every now-unpinned published product via PinterestBackfillService,
     which uses the correct path that DOES set the listing_url.

Products that already have a properly-linked pin are left alone (no duplicates).

Dry-run by default. Apply with:  python scripts/repin_catalog_with_links.py --apply
"""
import argparse
import asyncio
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from app.db.database import SessionLocal
from app.models.marketing_post import MarketingPost


async def _pin_link(pin_id: str):
    """Return (status, link) for a pin. status: 'ok'|'gone'|'error'."""
    import httpx
    from app.services.pinterest_oauth import get_valid_access_token, api_base
    tok = await get_valid_access_token()
    async with httpx.AsyncClient(timeout=30) as cl:
        r = await cl.get(f"{api_base()}/pins/{pin_id}", headers={"Authorization": f"Bearer {tok}"})
        if r.status_code == 404:
            return "gone", None
        if r.status_code >= 400:
            return "error", None
        return "ok", (r.json() or {}).get("link")


async def _delete_pin(pin_id: str) -> bool:
    import httpx
    from app.services.pinterest_oauth import get_valid_access_token, api_base
    tok = await get_valid_access_token()
    async with httpx.AsyncClient(timeout=30) as cl:
        r = await cl.delete(f"{api_base()}/pins/{pin_id}", headers={"Authorization": f"Bearer {tok}"})
        return r.status_code in (200, 204)


def _linkless_pins():
    """All our successful pinterest pins whose live link is empty/None."""
    db = SessionLocal()
    try:
        rows = db.query(MarketingPost).filter(
            MarketingPost.channel == "pinterest",
            MarketingPost.status == "success",
            MarketingPost.external_id.isnot(None),
        ).all()
        recs = [(m.id, m.external_id, m.task_id) for m in rows]
    finally:
        db.close()
    out = []
    for mid, pin_id, task_id in recs:
        try:
            status, link = asyncio.run(_pin_link(pin_id))
        except Exception:
            status, link = "error", None
        if status == "gone" or (status == "ok" and not link):
            out.append({"marketing_id": mid, "pin_id": pin_id, "task_id": task_id, "status": status})
        time.sleep(0.3)  # be gentle on the API
    return out


def _downgrade(marketing_id: str, new_status: str):
    db = SessionLocal()
    try:
        m = db.query(MarketingPost).filter(MarketingPost.id == marketing_id).first()
        if m:
            m.status = new_status
            db.commit()
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete + re-pin (default: dry run)")
    ap.add_argument("--limit", type=int, default=200, help="max products to re-pin")
    args = ap.parse_args()

    print("Scanning existing Pinterest pins for missing links...")
    dead = _linkless_pins()
    print(f"  linkless/dead pins found: {len(dead)}")
    for d in dead[:10]:
        print(f"    pin {d['pin_id']} (task {str(d['task_id'])[:8]}, {d['status']})")
    if len(dead) > 10:
        print(f"    ... and {len(dead) - 10} more")

    if not args.apply:
        print("\nDRY RUN — no changes. Re-run with --apply to delete these + re-pin with links.")
        return

    # 1) delete dead pins + free up their product for re-pinning
    deleted = 0
    for d in dead:
        ok = True
        if d["status"] == "ok":  # still exists on Pinterest -> delete it
            try:
                ok = asyncio.run(_delete_pin(d["pin_id"]))
            except Exception:
                ok = False
            time.sleep(0.3)
        if ok:
            _downgrade(d["marketing_id"], "deleted_no_link")
            deleted += 1
    print(f"\nDeleted {deleted} dead pin(s); their products are now re-pin candidates.")

    # 2) re-pin every published product lacking a good pin (fresh pins carry links)
    from app.services.pinterest_backfill_service import PinterestBackfillService
    rep = PinterestBackfillService().run(apply=True, limit=args.limit, sleep_seconds=3.0,
                                         rewrite_caption=True, include_already_pinned=False)
    print(f"Re-pinned {rep.get('posted', 0)} product(s) WITH links "
          f"(of {rep.get('total_candidates', 0)} candidates).")
    if rep.get("error"):
        print("  error:", rep["error"])


if __name__ == "__main__":
    main()
