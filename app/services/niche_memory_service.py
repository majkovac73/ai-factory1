"""
NicheMemoryService — the factory's persistent, self-updating learning memory.

The existing `_load_insights_block` already learns from views/revenue at the
FORMAT + KEYWORD level and re-derives it every cycle. This adds durable,
accumulating memory at the THEME/NICHE level: it groups every published product
by its niche (occasion, else its primary SEO keyword), measures what actually
gets VIEWS / FAVORITES / SALES, writes a verdict (winner / loser / unproven) per
niche to a persisted file on the volume, and hands the concept generator a
"double down on what works, stop making what doesn't" focus block.

It runs on its own: `update()` is called after the daily listing-stats poll, so
the factory keeps learning and re-focusing without a human. It fails SAFE — until
there's real traffic (>= LEARNING_MIN_VIEWS_FOR_SIGNAL views or any sale) it draws
no conclusions and injects nothing, so it never overfits to 1-view noise.
"""
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.paths import get_data_dir
from config import settings

logger = logging.getLogger("ai-factory")

_STOPWORDS = {"the", "a", "an", "for", "and", "with", "of", "to", "in", "on", "gift",
              "printable", "digital", "download", "instant", "card", "print", "set"}


class NicheMemoryService:
    FILE = "niche_memory.json"

    def __init__(self):
        self._dir = get_data_dir()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _path(self) -> Path:
        return self._dir / self.FILE

    # ── theme derivation ─────────────────────────────────────────────────────
    @staticmethod
    def _normalize(text: str) -> str:
        toks = [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t and t not in _STOPWORDS]
        return " ".join(toks[:3]).strip()

    @classmethod
    def theme_of(cls, task) -> str:
        """A coarse but meaningful niche bucket for a product. Prefers the seasonal
        occasion; else the primary SEO keyword (the main niche phrase); else format."""
        md = getattr(task, "metadata_", None) or {}
        occ = md.get("occasion")
        if occ:
            return f"occasion:{occ}"
        out = getattr(task, "output_data", None) or {}
        kws = out.get("keywords") or []
        if kws:
            n = cls._normalize(str(kws[0]))
            if n:
                return f"niche:{n}"
        # fall back to a normalized product name, then the format
        n = cls._normalize(out.get("title") or getattr(task, "title", "") or "")
        if n:
            return f"niche:{n}"
        return f"format:{getattr(task, 'type', 'unknown')}"

    # ── learn ────────────────────────────────────────────────────────────────
    def update(self, record: bool = True) -> dict:
        """Recompute per-niche performance from real listing views/favorites/revenue
        and persist it. Called autonomously after the daily stats poll. Returns the
        memory dict. Best-effort — never raises into the caller."""
        try:
            from app.core.product_formats import PRODUCT_FORMATS
            from app.services.task_service import TaskService
            from app.services.analytics_service import AnalyticsService
            from app.services.revenue_service import RevenueService

            an = AnalyticsService()
            rev_by_task = RevenueService().get_revenue_by_task() or {}
            tasks = [t for t in TaskService().list_tasks()
                     if t.type in PRODUCT_FORMATS and (t.output_data or {}).get("listing_id")]

            themes: dict = {}
            shop_views = 0
            shop_rev = 0.0
            for t in tasks:
                # latest lifetime views/favorites for this listing
                evs = an.get_events(event_type="listing_stats", entity_type="task", entity_id=t.id, limit=1)
                p = (evs[0].payload if evs else {}) or {}
                views = int(p.get("views", 0) or 0)
                faves = int(p.get("favorites", 0) or 0)
                rev = float(rev_by_task.get(t.id, 0.0) or 0.0)
                shop_views += views
                shop_rev += rev
                key = self.theme_of(t)
                th = themes.setdefault(key, {"n_listings": 0, "views": 0, "favorites": 0,
                                             "revenue": 0.0, "sales": 0})
                th["n_listings"] += 1
                th["views"] += views
                th["favorites"] += faves
                th["revenue"] = round(th["revenue"] + rev, 2)
                if rev > 0:
                    th["sales"] += 1

            view_floor = int(getattr(settings, "LEARNING_MIN_VIEWS_FOR_SIGNAL", 50))
            trustworthy = (shop_rev > 0) or (shop_views >= view_floor)

            # per-theme averages + verdicts (only meaningful once trustworthy)
            avgs = [th["views"] / th["n_listings"] for th in themes.values() if th["n_listings"]]
            median_avg = sorted(avgs)[len(avgs) // 2] if avgs else 0.0
            win_mult = float(getattr(settings, "NICHE_WINNER_VIEW_MULTIPLE", 1.5))
            win_min = int(getattr(settings, "NICHE_MIN_LISTINGS_FOR_VERDICT", 2))
            lose_min = int(getattr(settings, "NICHE_LOSER_MIN_LISTINGS", 3))

            for th in themes.values():
                n = th["n_listings"] or 1
                th["avg_views"] = round(th["views"] / n, 2)
                verdict = "unproven"
                if trustworthy:
                    if th["revenue"] > 0:
                        verdict = "winner"
                    elif th["n_listings"] >= win_min and th["avg_views"] >= max(median_avg * win_mult, 1.0):
                        verdict = "winner"
                    elif th["n_listings"] >= lose_min and th["revenue"] == 0 and th["avg_views"] <= max(median_avg * 0.5, 1.0):
                        verdict = "loser"
                th["verdict"] = verdict

            memory = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "signal_trustworthy": trustworthy,
                "shop_total_views": shop_views,
                "shop_total_revenue": round(shop_rev, 2),
                "median_avg_views": round(median_avg, 2),
                "themes": themes,
            }
            self._save(memory)
            if record:
                try:
                    winners = [k for k, v in themes.items() if v["verdict"] == "winner"]
                    losers = [k for k, v in themes.items() if v["verdict"] == "loser"]
                    an.record_event(event_type="niche_memory_updated", entity_type="shop", entity_id="shop",
                                    value=float(len(winners)),
                                    payload={"trustworthy": trustworthy, "winners": winners[:20],
                                             "losers": losers[:20], "shop_views": shop_views})
                except Exception:
                    pass
            logger.info(f"NicheMemoryService: learned {len(themes)} niches "
                        f"(trustworthy={trustworthy}, shop_views={shop_views}, "
                        f"winners={sum(1 for v in themes.values() if v['verdict']=='winner')}, "
                        f"losers={sum(1 for v in themes.values() if v['verdict']=='loser')})")
            return memory
        except Exception as e:
            logger.warning(f"NicheMemoryService: update failed: {e}")
            return self.load()

    # ── read ─────────────────────────────────────────────────────────────────
    def load(self) -> dict:
        p = self._path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"signal_trustworthy": False, "themes": {}, "shop_total_views": 0}

    def _save(self, memory: dict):
        p = self._path()
        tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(memory), encoding="utf-8")
        os.replace(tmp, p)

    @staticmethod
    def _pretty(theme_key: str) -> str:
        return theme_key.split(":", 1)[-1] if ":" in theme_key else theme_key

    def focus_block(self) -> str:
        """Concept-prompt block naming the proven winning/losing niches. Returns ''
        until there's real traffic, so the factory isn't steered by noise."""
        mem = self.load()
        if not mem.get("signal_trustworthy"):
            return ""
        themes = mem.get("themes") or {}
        winners = sorted(((k, v) for k, v in themes.items() if v.get("verdict") == "winner"),
                         key=lambda kv: (kv[1].get("revenue", 0), kv[1].get("avg_views", 0)), reverse=True)[:6]
        losers = sorted(((k, v) for k, v in themes.items() if v.get("verdict") == "loser"),
                        key=lambda kv: kv[1].get("n_listings", 0), reverse=True)[:6]
        if not winners and not losers:
            return ""
        parts = ["LEARNED FROM THIS SHOP'S REAL TRAFFIC (double down on what works):"]
        if winners:
            ws = ", ".join(
                f"'{self._pretty(k)}' ({'€%.0f, ' % v['revenue'] if v.get('revenue') else ''}{v.get('views',0)} views/{v.get('n_listings')} listings)"
                for k, v in winners)
            parts.append("PROVEN WINNERS — propose a NEW, original product in these niches (not a copy): " + ws + ".")
        if losers:
            ls = ", ".join(f"'{self._pretty(k)}'" for k, _ in losers)
            parts.append("PROVEN DEAD ENDS — do NOT propose more of these (they get little/no traffic): " + ls + ".")
        return " ".join(parts)
