"""
AdCandidateService — Etsy Ads has NO API (Etsy controls it), so the factory can't
run ads itself. What it CAN do is tell the operator EXACTLY which listings to
promote, so the manual ad budget goes to the listings most likely to earn back
their click cost.

Ranking blends the signals that predict ad ROI:
  * engagement velocity — buyers already viewing/favoriting it (strongest signal),
  * ticket size — a higher-priced format returns more per converted click,
  * in-season — promote seasonal items inside their buying window, not off-season,
  * freshness — a brand-new listing has no organic footing yet, so paid help
    compounds better than on a stale one.

Everything is best-effort and explainable: each candidate carries a plain-English
reason so the operator understands the pick.
"""
import logging

logger = logging.getLogger("ai-factory")


class AdCandidateService:
    def recommend(self, limit: int = 10) -> dict:
        from datetime import datetime
        from app.core.product_formats import PRODUCT_FORMATS, price_band_for
        from app.services.task_service import TaskService
        from app.services.performance_service import PerformanceService
        from app.services.revenue_service import RevenueService
        try:
            from app.core.seasonality import occasion_for, occasion_in_window
        except Exception:
            occasion_for = lambda *a, **k: None       # noqa: E731
            occasion_in_window = lambda *a, **k: True  # noqa: E731

        perf = PerformanceService()
        pbf = RevenueService().profit_by_format() or {}

        rows = []
        for t in TaskService().list_tasks():
            if t.type not in PRODUCT_FORMATS:
                continue
            out = t.output_data or {}
            listing_id = out.get("listing_id")
            if not listing_id:
                continue  # only live listings can be promoted

            velocity = 0.0
            try:
                velocity = float(perf.engagement_velocity(t.id) or 0.0)
            except Exception:
                pass

            # ticket size: real avg sale price for the format, else band midpoint
            fmt_stats = pbf.get(t.type) or {}
            price = fmt_stats.get("avg_price")
            if not price:
                lo, hi = price_band_for(t.type)
                price = (lo + hi) / 2.0

            title = out.get("title") or t.title or ""
            desc = out.get("description") or ""
            occ = None
            try:
                occ = occasion_for(title, desc)
            except Exception:
                pass
            in_season = True if not occ else bool(occasion_in_window(occ))

            age_days = 999.0
            if t.created_at:
                age_days = max(0.0, (datetime.utcnow() - t.created_at).total_seconds() / 86400.0)

            rows.append({
                "task_id": t.id, "listing_id": listing_id, "title": title[:80],
                "product_format": t.type, "velocity": round(velocity, 2),
                "price": round(float(price), 2), "occasion": occ,
                "in_season": in_season, "age_days": round(age_days, 1),
            })

        if not rows:
            return {"count": 0, "candidates": [], "note": "No published listings to promote yet."}

        # normalize velocity + price across the set, then a weighted, explainable score
        max_v = max((r["velocity"] for r in rows), default=0.0) or 1.0
        max_p = max((r["price"] for r in rows), default=0.0) or 1.0
        for r in rows:
            nv = r["velocity"] / max_v
            npr = r["price"] / max_p
            fresh = 1.0 if r["age_days"] <= 14 else (0.6 if r["age_days"] <= 45 else 0.3)
            season = 1.0 if r["in_season"] else 0.15  # strongly de-prioritize off-season
            score = (0.45 * nv + 0.30 * npr + 0.15 * fresh + 0.10 * (1.0 if r["in_season"] else 0.0)) * season
            r["promote_score"] = round(score * 100, 1)
            reasons = []
            if r["velocity"] > 0:
                reasons.append(f"already getting engagement ({r['velocity']}/day)")
            reasons.append(f"€{r['price']:.0f} ticket ({r['product_format'].replace('_', ' ')})")
            if r["occasion"]:
                reasons.append(f"in-season for {r['occasion'].replace('_', ' ')}" if r["in_season"]
                               else f"OFF-SEASON for {r['occasion'].replace('_', ' ')} — hold")
            if r["age_days"] <= 14:
                reasons.append("fresh listing (paid help compounds)")
            r["why"] = "; ".join(reasons)

        rows.sort(key=lambda r: r["promote_score"], reverse=True)
        top = rows[: max(1, int(limit))]
        return {
            "count": len(rows),
            "note": ("Etsy Ads has no API — promote these in Shop Manager > Marketing > Etsy Ads. "
                     "Ranked by likely ad ROI (engagement + ticket size + in-season + freshness)."),
            "candidates": top,
            "etsy_ads_urls": [f"https://www.etsy.com/listing/{r['listing_id']}" for r in top],
        }
