"""
Marketing-refresh service — periodically re-promotes EXISTING published
products using their ALREADY-GENERATED assets (no new image generation).

Why this exists: product marketing currently fires once, at creation. Listings
then go stale with no ongoing promotion. This rotates through the real,
published catalog and re-posts each product to a marketing channel using assets
already on disk — so it stays cheap regardless of how often it runs. The only
spend is one optional, small caption-rewrite LLM call per post (so repeated
posts about the same product don't read as identical copy-paste).

"Published product" = a Task that reached DONE, has a recognized product_format,
and has a real, readback-verified etsy_listing_id persisted on its catalog
assets (ImageAsset.listing_id) or its POD product (PODProduct.etsy_listing_id).
Blocked tasks never get a listing_id attached, so they're excluded for free.

Refresh-state (last-marketed timestamp per product+channel) is derived from the
existing MarketingPost table rather than a duplicate table — MarketingPost
already records task_id/channel/status/created_at for every post, and the
refresh posts themselves go through MarketingService, which writes those rows.
So posting IS the state update; there's nothing to keep in sync.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from app.core.product_formats import PRODUCT_FORMATS
from app.db.database import SessionLocal
from app.models.image_asset import ImageAsset
from app.models.marketing_post import MarketingPost
from app.models.pod_product import PODProduct
from app.models.task import Task
from app.schemas.enums import TaskStatus
from app.services.image_catalog_service import ImageCatalogService
from app.services.marketing_service import MarketingService
from config import settings

logger = logging.getLogger("ai-factory")


class MarketingRefreshCandidate:
    def __init__(self, task: Task, listing_id: str, last_marketed_at: Optional[datetime], engagement: float = 0.0):
        self.task = task
        self.task_id = task.id
        self.listing_id = listing_id
        self.last_marketed_at = last_marketed_at
        self.engagement = engagement  # D-6: views + 10*favorites (A-10)


class MarketingRefreshService:
    def __init__(self, catalog: Optional[ImageCatalogService] = None, marketing: Optional[MarketingService] = None, provider=None):
        self.catalog = catalog or ImageCatalogService()
        self.marketing = marketing or MarketingService()
        self._provider = provider  # text LLM for caption rewrite; lazy

    # ── Candidate selection ───────────────────────────────────────────────────

    def resolve_listing_id(self, task_id: str) -> Optional[str]:
        """The task's real, verified etsy_listing_id from any persisted source."""
        db = SessionLocal()
        try:
            pod = (
                db.query(PODProduct)
                .filter(PODProduct.task_id == task_id, PODProduct.etsy_listing_id.isnot(None))
                .first()
            )
            if pod and pod.etsy_listing_id:
                return pod.etsy_listing_id
            asset = (
                db.query(ImageAsset)
                .filter(ImageAsset.task_id == task_id, ImageAsset.listing_id.isnot(None))
                .first()
            )
            return asset.listing_id if asset else None
        finally:
            db.close()

    def last_marketed_at(self, task_id: str) -> Optional[datetime]:
        """Timestamp of the most recent SUCCESSFUL marketing post for this task,
        across ANY channel. None if never successfully marketed."""
        db = SessionLocal()
        try:
            row = (
                db.query(MarketingPost.created_at)
                .filter(MarketingPost.task_id == task_id, MarketingPost.status == "success")
                .order_by(MarketingPost.created_at.desc())
                .first()
            )
            return row[0] if row else None
        finally:
            db.close()

    def select_candidates(self, limit: int) -> List[MarketingRefreshCandidate]:
        """Least-recently-marketed published products first (fair rotation).

        A candidate is a DONE task with a recognized product_format and a real
        etsy_listing_id, whose last successful marketing post (any channel) is
        null or older than MARKETING_REFRESH_MIN_INTERVAL_DAYS.
        """
        min_interval = timedelta(days=settings.MARKETING_REFRESH_MIN_INTERVAL_DAYS)
        cutoff = datetime.utcnow() - min_interval

        db = SessionLocal()
        try:
            tasks = (
                db.query(Task)
                .filter(Task.status == TaskStatus.DONE.value, Task.type.in_(list(PRODUCT_FORMATS.keys())))
                .order_by(Task.created_at.asc())
                .all()
            )
        finally:
            db.close()

        candidates: List[MarketingRefreshCandidate] = []
        for task in tasks:
            listing_id = self.resolve_listing_id(task.id)
            if not listing_id:
                continue  # not a genuinely published product (or was blocked)
            last = self.last_marketed_at(task.id)
            if last is not None and last > cutoff:
                continue  # marketed too recently
            candidates.append(MarketingRefreshCandidate(task, listing_id, last, self._engagement(task.id)))

        # D-6: never-marketed (None) first, then WEIGHT toward products with the
        # most engagement (views + 10*favorites from A-10) — re-promote proven
        # products harder — then oldest-marketed as the tiebreaker.
        candidates.sort(key=lambda c: (c.last_marketed_at is not None, -c.engagement, c.last_marketed_at or datetime.min))
        return candidates[:limit]

    def _engagement(self, task_id: str) -> float:
        """Latest listing_stats value (views + 10*favorites) for a task, or 0."""
        try:
            from app.services.analytics_service import AnalyticsService
            events = AnalyticsService().get_events(
                event_type="listing_stats", entity_type="task", entity_id=task_id, limit=1
            )
            return float(events[0].value or 0) if events else 0.0
        except Exception:
            return 0.0

    # ── Refresh a single product on a channel ─────────────────────────────────

    def _pick_asset_path(self, task_id: str) -> Optional[str]:
        """An existing image asset for the product — prefer a listing photo
        (a real image), fall back to the delivery asset (may be a PDF, which the
        channel converts to a PNG first page). No regeneration."""
        from pathlib import Path

        listing_assets = self.catalog.get_listing_assets(task_id)
        for a in listing_assets:
            if a.use_case == "listing" and Path(a.local_path).exists():
                return a.local_path
        for a in listing_assets:
            if Path(a.local_path).exists():
                return a.local_path
        delivery = self.catalog.get_delivery_asset(task_id)
        if delivery and Path(delivery.local_path).exists():
            return delivery.local_path
        return None

    def _listing_url(self, listing_id: str) -> str:
        return f"https://www.etsy.com/listing/{listing_id}"

    def rewrite_caption(self, title: str, description: str) -> Optional[str]:
        """ONE cheap text-LLM call to reword the promo caption so repeat posts
        don't read as identical. Returns None on any failure (caller falls back
        to the original description). Cost: a few hundred tokens on
        settings.DEFAULT_MODEL (gpt-4o-mini) ≈ $0.0001–0.0003 per call."""
        import asyncio

        from app.core.providers.manager import ProviderManager

        provider = self._provider or ProviderManager.get_provider()
        prompt = (
            f"Rewrite this product's promo blurb as ONE fresh, upbeat social caption "
            f"(max 2 sentences, no hashtags, no quotes). Product: {title}. "
            f"Current blurb: {description}"
        )
        try:
            text = asyncio.run(provider.generate(model=settings.DEFAULT_MODEL, prompt=prompt, temperature=0.9, max_tokens=120))
            return (text or "").strip() or None
        except Exception as e:
            logger.warning(f"MarketingRefreshService: caption rewrite failed, using original: {e}")
            return None

    def refresh_post(self, task_id: str, channel, listing_id: Optional[str] = None, rewrite_caption: bool = True) -> dict:
        """Re-promote one existing product on one channel using existing assets.

        No image generation. Records a MarketingPost via MarketingService (which
        also updates the derived last-marketed state). Returns the channel result.
        """
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            output = (task.output_data or {}) if task else {}
        finally:
            db.close()

        listing_id = listing_id or self.resolve_listing_id(task_id)
        title = output.get("title") or (task.title if task else "") or "Product"
        description = output.get("description") or ""
        keywords = output.get("keywords") or []

        asset_path = self._pick_asset_path(task_id)
        if not asset_path:
            return {"success": False, "external_id": None, "url": None, "error": "no existing asset found to re-promote"}

        caption = description
        if rewrite_caption:
            new_caption = self.rewrite_caption(title, description)
            if new_caption:
                caption = new_caption

        listing = {
            "title": title,
            "description": caption,
            "keywords": keywords,
            "image_path": asset_path,
            "listing_url": self._listing_url(listing_id) if listing_id else "",
        }
        return self.marketing.post_to_channel(task_id, listing, channel)

    # ── One refresh cycle ─────────────────────────────────────────────────────

    def run_cycle(self, channels: list, max_posts: Optional[int] = None, rewrite_caption: bool = True) -> dict:
        """Post the least-recently-marketed products to the given channel(s),
        capped at MARKETING_REFRESH_MAX_POSTS_PER_CYCLE total posts.

        The cap is on total posts, so one run can never spam the whole catalog
        or every channel at once.
        """
        cap = max_posts if max_posts is not None else settings.MARKETING_REFRESH_MAX_POSTS_PER_CYCLE
        if cap <= 0 or not channels:
            return {"posted": 0, "results": []}

        candidates = self.select_candidates(limit=cap)
        results = []
        posted = 0
        for cand in candidates:
            if posted >= cap:
                break
            # One post per candidate this cycle (primary channel first), so the
            # cycle rotates across DIFFERENT products rather than hammering one.
            channel = channels[posted % len(channels)]
            result = self.refresh_post(cand.task_id, channel, listing_id=cand.listing_id, rewrite_caption=rewrite_caption)
            results.append({"task_id": cand.task_id, "channel": channel.name, "success": result.get("success"), "url": result.get("url"), "error": result.get("error")})
            posted += 1

        logger.info(f"MarketingRefreshService: cycle posted {posted} product(s)")
        return {"posted": posted, "results": results}
