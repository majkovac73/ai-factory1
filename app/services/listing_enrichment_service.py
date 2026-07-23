"""
ListingEnrichmentService — rewrite thin listing descriptions into full-length,
keyword-rich, conversion-oriented copy and push them live to Etsy.

Etsy indexes descriptions for search AND buyers read them before purchase, yet
most of this shop's listings shipped with ~45-word descriptions (well under the
130-200 word target). This service regenerates a proper description body (hook →
what's included → who it's for → how to use → why it's better), appends the
deterministic per-format blocks + AI disclosure (mirroring the create path), and
PATCHes the listing. Best-effort and idempotent-ish: safe to re-run.
"""
import asyncio
import logging
from typing import Optional

from config import settings

logger = logging.getLogger("ai-factory")

# a description shorter than this is considered "thin" and worth rewriting
DEFAULT_MIN_CHARS = 650
# the LLM body alone should reach at least this before we append blocks
_BODY_MIN_CHARS = 600


class ListingEnrichmentService:
    def __init__(self, generator=None):
        # default text model; a focused description-only prompt makes length
        # reliable even on the cheap model (no competing JSON fields).
        if generator is not None:
            self.gen = generator
        else:
            from app.agents.base_agent import BaseAgent
            self.gen = BaseAgent(model=getattr(settings, "SEO_MODEL", None) or settings.DEFAULT_MODEL)

    @staticmethod
    def _is_pod(product_format: str) -> bool:
        return str(product_format or "").startswith("pod_")

    def build_description(self, product_name: str, product_format: str, keywords: list,
                          page_count: Optional[int] = None) -> str:
        kws = [str(k) for k in (keywords or []) if k]
        primary = kws[0] if kws else product_name
        is_pod = self._is_pod(product_format)
        medium = "a physical item printed to order and shipped" if is_pod else "an INSTANT DIGITAL DOWNLOAD (no physical item is shipped)"
        fmt_h = str(product_format or "product").replace("_", " ")

        prompt = f"""You are an expert Etsy SEO copywriter. Write ONLY the description BODY
for this listing (no title, no JSON, no markdown headers, no quotes).

Product name: {product_name}
Format: {fmt_h}
Primary search keyword: {primary}
Keywords to weave in naturally: {', '.join(kws[:8])}
This is {medium}.

Requirements:
- 150-200 words (about 800-1100 characters). Specific to THIS product — never generic.
- FIRST sentence: a benefit-driven hook that contains the primary keyword (Etsy
  weights the opening line and uses it as the search snippet).
- Then, in short scannable sentences: what it is / what's included, who it's for,
  how they'll use it, and why it's better than the alternatives.
- Weave in 4-6 of the keywords naturally (no keyword stuffing).
- Warm, confident, persuasive. Return ONLY the description text."""

        text = (self.gen._generate(prompt) or "").strip()
        if len(text) < _BODY_MIN_CHARS:
            text2 = (self.gen._generate(
                prompt + "\n\nYour previous draft was too short. Rewrite it fuller, "
                         "reaching a complete 180-200 words with concrete specifics.") or "").strip()
            if len(text2) > len(text):
                text = text2

        # append deterministic per-format blocks + honest AI disclosure (same as
        # the create path) so enriched listings match freshly-created ones.
        try:
            from app.core.product_formats import description_blocks
            blocks = description_blocks(product_format, page_count)
            if blocks and "WHAT YOU GET" not in text:
                text = text.rstrip() + "\n\n" + blocks
        except Exception:
            pass
        disc = getattr(settings, "SHOP_AI_DISCLOSURE", "")
        if disc and disc.lower() not in text.lower():
            text = text.rstrip() + f"\n\n{disc}"
        return text.strip()

    def enrich_task(self, task, apply: bool) -> dict:
        out = task.output_data or {}
        listing_id = out.get("listing_id")
        current = out.get("description") or ""
        pc = (task.metadata_ or {}).get("page_count") or out.get("page_count")
        new_desc = self.build_description(
            out.get("title") or task.title or "Product", task.type,
            out.get("keywords") or [], page_count=pc)
        result = {"task_id": task.id, "listing_id": listing_id,
                  "old_len": len(current), "new_len": len(new_desc), "applied": False}
        if apply and listing_id and len(new_desc) > len(current):
            try:
                asyncio.run(self._patch(listing_id, new_desc))
                self._persist_description(task.id, new_desc)  # keep our copy in sync
                result["applied"] = True
            except Exception as e:
                result["error"] = str(e)[:200]
        return result

    @staticmethod
    def _persist_description(task_id: str, description: str):
        """Best-effort: update the stored output_data.description so re-runs skip
        it and marketing/readbacks use the rich copy."""
        try:
            from app.db.database import SessionLocal
            from app.models.task import Task
            db = SessionLocal()
            try:
                t = db.query(Task).filter(Task.id == task_id).first()
                if t:
                    merged = dict(t.output_data or {})
                    merged["description"] = description
                    t.output_data = merged
                    db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"ListingEnrichmentService: could not persist description for {task_id}: {e}")

    async def _patch(self, listing_id: str, description: str):
        from app.services.etsy_client import EtsyClient
        await EtsyClient().update_listing(listing_id, {"description": description[:65000]})

    def enrich_all(self, apply: bool = False, min_chars: int = DEFAULT_MIN_CHARS,
                   limit: int = 500) -> dict:
        from app.core.product_formats import PRODUCT_FORMATS
        from app.services.task_service import TaskService
        import time

        tasks = [t for t in TaskService().list_tasks()
                 if t.type in PRODUCT_FORMATS and (t.output_data or {}).get("listing_id")]
        thin = [t for t in tasks if len((t.output_data or {}).get("description") or "") < min_chars]
        plan = thin[: max(1, int(limit))]
        report = {"published": len(tasks), "thin": len(thin), "to_enrich": len(plan),
                  "applied": apply, "enriched": 0, "results": []}
        for i, t in enumerate(plan):
            r = self.enrich_task(t, apply=apply)
            report["results"].append(r)
            if r.get("applied"):
                report["enriched"] += 1
            if apply and i < len(plan) - 1:
                time.sleep(1.0)  # gentle on Etsy + LLM
        logger.info(f"ListingEnrichmentService: enriched {report['enriched']}/{len(plan)} "
                    f"(of {len(thin)} thin, {len(tasks)} published)")
        return report
