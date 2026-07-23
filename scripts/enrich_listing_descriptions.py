"""
Rewrite thin listing descriptions into full-length, keyword-rich copy and push
them live to Etsy. Thin descriptions rank + convert worse — this brings every
published listing up to the 130-200 word target so ad/organic traffic converts.

Dry-run by default (shows old vs new length per listing, spends nothing on Etsy).
Apply with:  python scripts/enrich_listing_descriptions.py --apply
Options:  --min-chars N (thin threshold, default 650)  --limit N
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="generate + PATCH live (default: dry run)")
    ap.add_argument("--min-chars", type=int, default=650, help="rewrite listings whose description is shorter than this")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    from app.services.listing_enrichment_service import ListingEnrichmentService
    svc = ListingEnrichmentService()

    if not args.apply:
        # dry run: report which listings are thin + a sample rewrite length
        from app.core.product_formats import PRODUCT_FORMATS
        from app.services.task_service import TaskService
        tasks = [t for t in TaskService().list_tasks()
                 if t.type in PRODUCT_FORMATS and (t.output_data or {}).get("listing_id")]
        thin = [t for t in tasks if len((t.output_data or {}).get("description") or "") < args.min_chars]
        print(f"published listings: {len(tasks)} | thin (<{args.min_chars} chars): {len(thin)}")
        for t in thin[:8]:
            cur = len((t.output_data or {}).get("description") or "")
            print(f"  {t.type:22} cur={cur:4}  '{((t.output_data or {}).get('title') or '')[:50]}'")
        if len(thin) > 8:
            print(f"  ... and {len(thin) - 8} more")
        print(f"\nDRY RUN — nothing changed. Re-run with --apply to rewrite + push live.")
        return

    report = svc.enrich_all(apply=True, min_chars=args.min_chars, limit=args.limit)
    print(f"Published: {report['published']} | thin: {report['thin']} | enriched: {report['enriched']}")
    for r in report["results"]:
        tag = "OK " if r.get("applied") else ("ERR" if r.get("error") else "-- ")
        extra = f" ERROR: {r['error']}" if r.get("error") else ""
        print(f"  [{tag}] {str(r['listing_id']):>12}  {r['old_len']:>4} -> {r['new_len']:>4} chars{extra}")


if __name__ == "__main__":
    main()
