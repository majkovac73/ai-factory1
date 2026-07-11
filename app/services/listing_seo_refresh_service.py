"""
ListingSeoRefreshService (STEP 104 7-4) — give zero-view listings a second
chance at ranking.

A listing that has sat active for 21+ days with <5 views is invisible: Etsy's
search isn't showing it to anyone. Rewriting its title/tags ONCE, using the
phrases that currently-winning listings in the same niche actually rank for
(EtsyMarketService.top_titles), is a near-free (~$0 — deterministic here)
second attempt. update_listing (PATCH) already exists.

Rules:
  - age >= SEO_REFRESH_MIN_AGE_DAYS (21) AND views < SEO_REFRESH_MAX_VIEWS (5)
  - each listing is refreshed AT MOST ONCE (idempotent marker event), so this
    can't thrash a listing's SEO every run.
  - deterministic: new tags are the proven 2-3 word n-grams mined from the
    real winning titles (trademark-filtered), merged with the listing's
    existing tags; the single strongest missing phrase is promoted to the FRONT
    of the title (Etsy weights the title start most). No LLM, no image-gen.
  - DRY-RUN by default (apply=False reports what it WOULD do); apply=True PATCHes.
"""
import asyncio
import logging
import time

from app.services.analytics_service import AnalyticsService
from app.services.listing_stats_service import ListingStatsService
from config import settings

logger = logging.getLogger("ai-factory")

ETSY_TITLE_MAX = 140


class ListingSeoRefreshService:
    def __init__(self):
        self.analytics = AnalyticsService()

    # ── idempotency ──────────────────────────────────────────────────────────
    def _already_refreshed(self, listing_id: str) -> bool:
        events = self.analytics.get_events(
            event_type="seo_refreshed", entity_type="listing",
            entity_id=str(listing_id), limit=1,
        )
        return len(events) > 0

    def _mark_refreshed(self, listing_id: str, task_id, old_title: str, new_title: str, new_tags: list):
        self.analytics.record_event(
            event_type="seo_refreshed",
            entity_type="listing",
            entity_id=str(listing_id),
            value=1.0,
            payload={"task_id": task_id, "old_title": old_title,
                     "new_title": new_title, "new_tags": new_tags},
        )

    # ── keyword query for the market lookup ──────────────────────────────────
    @staticmethod
    def _query_from_listing(listing: dict) -> str:
        """Derive a niche keyword query from the listing's own title (first few
        content words) so top_titles reflects THIS listing's competitors."""
        title = (listing.get("title") or "").lower()
        stop = {"the", "and", "for", "with", "your", "you", "our", "a", "an", "of",
                "to", "in", "on", "printable", "digital", "instant", "download"}
        words = [w for w in "".join(c if c.isalnum() or c.isspace() else " " for c in title).split()
                 if len(w) > 2 and w not in stop]
        return " ".join(words[:4])

    # ── the actual rewrite (deterministic, pure) ─────────────────────────────
    @staticmethod
    def build_refresh(current_title: str, current_tags: list, top_titles: list) -> dict:
        """Return {title, tags} rebuilt from the winning-title n-grams. Pure and
        deterministic so it's fully testable offline."""
        from app.agents.etsy.listing_generator import ListingGeneratorAgent
        ngrams = ListingGeneratorAgent.title_ngrams(top_titles, max_terms=8)

        # Tags: proven phrases first, then keep the listing's existing tags to
        # fill the 13 slots (existing tags may already carry niche specifics).
        gen = ListingGeneratorAgent()
        new_tags = gen._derive_tags(
            keywords=ngrams,
            product_name=current_title,
            extra_terms=[t for t in (current_tags or []) if isinstance(t, str)],
        )

        # Title: promote the single strongest proven phrase to the front if the
        # title doesn't already lead with it. Never exceed Etsy's 140 chars.
        new_title = current_title
        lead = ngrams[0] if ngrams else ""
        if lead and lead.lower() not in current_title.lower()[:len(lead) + 5]:
            candidate = f"{lead.title()} | {current_title}".strip()
            new_title = candidate[:ETSY_TITLE_MAX].rstrip(" |")

        return {"title": new_title, "tags": new_tags}

    # ── orchestration ────────────────────────────────────────────────────────
    def run(self, apply: bool = False) -> dict:
        try:
            listings = asyncio.run(ListingStatsService()._fetch_active_listings())
        except Exception as e:
            logger.error(f"ListingSeoRefreshService: fetch failed: {e}")
            return {"ok": False, "error": str(e)}

        now = time.time()
        min_age_days = getattr(settings, "SEO_REFRESH_MIN_AGE_DAYS", 21)
        max_views = getattr(settings, "SEO_REFRESH_MAX_VIEWS", 5)
        max_per_run = getattr(settings, "SEO_REFRESH_MAX_PER_RUN", 5)

        from app.services.etsy_market_service import EtsyMarketService
        from app.services.etsy_client import EtsyClient
        market = EtsyMarketService()

        refreshed, candidates = 0, []
        for listing in listings:
            if len(candidates) >= max_per_run:
                break
            listing_id = str(listing.get("listing_id", ""))
            if not listing_id:
                continue
            created = listing.get("created_timestamp") or listing.get("creation_tsz") or 0
            views = int(listing.get("views", 0) or 0)
            age_days = (now - int(created)) / 86400 if created else 0
            if age_days < min_age_days or views >= max_views:
                continue
            if self._already_refreshed(listing_id):
                continue

            query = self._query_from_listing(listing)
            summary = None
            try:
                summary = asyncio.run(market.validate_concept(query)) if query else None
            except Exception as e:
                logger.warning(f"ListingSeoRefreshService: market lookup failed for {listing_id}: {e}")
            top_titles = (summary or {}).get("top_titles") or []
            if not top_titles:
                continue  # no proven phrases to rewrite from — leave it alone

            plan = self.build_refresh(listing.get("title", ""), listing.get("tags", []), top_titles)
            entry = {"listing_id": listing_id, "views": views, "age_days": round(age_days, 1),
                     "old_title": listing.get("title", ""), "new_title": plan["title"],
                     "new_tags": plan["tags"]}
            candidates.append(entry)

            if apply:
                task_id = ListingStatsService._resolve_task_id(listing_id)
                try:
                    asyncio.run(EtsyClient().update_listing(
                        listing_id, {"title": plan["title"], "tags": plan["tags"]}))
                    self._mark_refreshed(listing_id, task_id, entry["old_title"],
                                         plan["title"], plan["tags"])
                    refreshed += 1
                except Exception as e:
                    logger.error(f"ListingSeoRefreshService: PATCH {listing_id} failed: {e}")

        report = {"ok": True, "active": len(listings), "candidates": candidates,
                  "refreshed": refreshed, "applied": apply}
        logger.info(f"ListingSeoRefreshService: {len(candidates)} candidates, "
                    f"refreshed={refreshed}, applied={apply}")
        return report
