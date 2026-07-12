"""
LowScoreCleanupService (STEP 105 1-2) — clean the shop face from an audit report.

audit_existing_listings.py scores every live listing 1-10 against the same critic
rubric. The re-audit found 22 of 28 listings below the pass bar, several for
occasions that passed months ago. Every buyer who lands on ANY listing sees this
grid — it suppresses conversion shop-wide, and each active listing pays $0.20
auto-renew per 4 months.

This reads that report JSON and:
  - deactivates listings scoring <= SHOP_CLEANUP_MAX_SCORE (default 3 — the
    critic's own "would erode trust in the shop" band) via
    EtsyClient.update_listing(state="inactive");
  - leaves the 4-5 scorers for the 7-4 SEO refresh to take one shot at;
  - keeps 6+.

DRY-RUN by default (report only, so Maj eyeballs the list before apply).
"""
import glob
import json
import logging
import os

from config import settings

logger = logging.getLogger("ai-factory")

REPORTS_DIR = os.path.join("instructions", "audit_reports")


class LowScoreCleanupService:
    @staticmethod
    def _latest_report_path() -> str:
        """Newest committed audit report, or None."""
        files = sorted(glob.glob(os.path.join(REPORTS_DIR, "*.json")))
        return files[-1] if files else None

    @staticmethod
    def _load(report_path: str) -> list:
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)
        # accept either a bare list or {listings:[...]} / {results:[...]}
        if isinstance(data, dict):
            data = data.get("listings") or data.get("results") or []
        return data or []

    def run(self, report_path: str = None, apply: bool = False) -> dict:
        report_path = report_path or self._latest_report_path()
        if not report_path or not os.path.exists(report_path):
            return {"ok": False, "error": "no audit report found", "report_path": report_path}

        max_score = int(getattr(settings, "SHOP_CLEANUP_MAX_SCORE", 3))
        rows = self._load(report_path)

        deactivate, seo_retry, keep = [], [], 0
        for r in rows:
            lid = str(r.get("listing_id", ""))
            score = r.get("score")
            if not lid or score is None:
                continue
            score = int(score)
            entry = {"listing_id": lid, "score": score, "title": (r.get("title") or "")[:70]}
            if score <= max_score:
                deactivate.append(entry)
            elif score <= 5:
                seo_retry.append(entry)
            else:
                keep += 1

        deactivated = 0
        if apply and deactivate:
            import asyncio
            from app.services.etsy_client import EtsyClient
            for c in deactivate:
                try:
                    asyncio.run(EtsyClient().update_listing(c["listing_id"], {"state": "inactive"}))
                    deactivated += 1
                except Exception as e:
                    logger.error(f"LowScoreCleanupService: deactivate {c['listing_id']} failed: {e}")

        report = {
            "ok": True, "report_path": report_path, "applied": apply,
            "max_score": max_score, "total": len(rows),
            "deactivate": deactivate, "deactivated": deactivated,
            "seo_retry": seo_retry, "keep": keep,
        }
        # dry-run reporting so a human sees the list before trusting it.
        if deactivate:
            try:
                from app.services.alert_service import AlertService
                verb = "Deactivated" if apply else "Low-score cleanup candidates (dry-run)"
                AlertService().send_alert_sync(
                    f"Shop cleanup — {verb}: {len(deactivate)} (<= {max_score}/10)",
                    "; ".join(f"{c['listing_id']} ({c['score']}/10) {c['title']}" for c in deactivate[:20]),
                    level="warning",
                )
            except Exception:
                pass
        logger.info(f"LowScoreCleanupService: {len(deactivate)} to deactivate, "
                    f"{len(seo_retry)} for SEO retry, {keep} kept, applied={apply}")
        return report
