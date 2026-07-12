"""
Deactivate low-scoring live listings from an audit report (STEP 105 1-2).

Reads the newest report in instructions/audit_reports/ (or --report PATH) and
deactivates every listing scoring <= SHOP_CLEANUP_MAX_SCORE (default 3). The 4-5
scorers are left for the 7-4 SEO refresh; 6+ are kept.

DRY-RUN by default. Eyeball the printed list, then re-run with --apply.

Usage:
  python scripts/cleanup_low_score_listings.py
  python scripts/cleanup_low_score_listings.py --report instructions/audit_reports/2026-07-12.json
  python scripts/cleanup_low_score_listings.py --apply
"""
import sys

sys.path.insert(0, ".")

from app.services.low_score_cleanup_service import LowScoreCleanupService


def _arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    report = _arg("--report")
    rep = LowScoreCleanupService().run(report_path=report, apply=apply)
    if not rep.get("ok"):
        print(f"ERROR: {rep.get('error')}")
        sys.exit(1)
    print(f"\n{'APPLIED' if apply else 'DRY RUN'} — shop low-score cleanup ({rep['report_path']})")
    print(f"  total scored     : {rep['total']}")
    print(f"  DEACTIVATE (<= {rep['max_score']}) : {len(rep['deactivate'])}"
          + (f"  (deactivated {rep['deactivated']})" if apply else ""))
    for c in rep["deactivate"]:
        print(f"     {c['score']}/10  {c['listing_id']}  {c['title']}")
    print(f"  SEO retry (4-5)  : {len(rep['seo_retry'])}")
    for c in rep["seo_retry"]:
        print(f"     {c['score']}/10  {c['listing_id']}  {c['title']}")
    print(f"  keep (6+)        : {rep['keep']}")
    if not apply and rep["deactivate"]:
        print("\nRe-run with --apply to deactivate the low scorers.")
