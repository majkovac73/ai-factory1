"""
TrendDataService — real market signal for TrendResearchAgent.

Replaces LLM-imagined trend data with actual Google Trends data (via the
unofficial pytrends client) for a fixed set of Etsy-relevant seed
categories. This is an unofficial API that scrapes Google Trends' public
frontend — it can rate-limit or fail. On failure this service raises;
callers must NOT silently fall back to guessing.
"""
import logging
import time
import random

from pytrends.request import TrendReq

from config import settings

logger = logging.getLogger("ai-factory")

# Etsy-relevant seed categories. Edit this list over time as you learn what
# converts — this is not meant to be exhaustive, just a real anchor point.
# Can be overridden without a code change via settings.TREND_SEED_KEYWORDS.
# P1-10: seeds must anchor research to what the image/PDF pipeline can ACTUALLY
# build. The old list steered toward svg files, editable templates and clipart
# BUNDLES (bundles are explicitly banned by the multi-item validator) — none of
# which are buildable, so cycles burned retries or produced mismatches.
SEED_KEYWORDS = [
    "printable wall art",
    "digital planner",
    "coloring pages",
    "phone wallpaper aesthetic",
    "sticker sheet",
    "greeting card printable",
    "funny t shirt",
    "budget planner printable",
]

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 5


class TrendDataFetchError(Exception):
    """Raised when real trend data cannot be obtained. Callers must treat
    this as a hard stop for the cycle, not a signal to fall back to
    LLM-guessed data."""
    pass


class TrendDataService:
    def __init__(self):
        self._pytrends = TrendReq(hl="en-US", tz=360)

    def _default_keywords(self) -> list:
        configured = getattr(settings, "TREND_SEED_KEYWORDS", None) or []
        base = list(configured) if configured else list(SEED_KEYWORDS)
        # A-7: fold in 1-2 in-season seed keywords so trend data reflects the
        # occasions buyers are shopping for now, not just evergreen categories.
        try:
            from app.core.seasonality import seasonal_seed_keywords
            for kw in seasonal_seed_keywords():
                if kw not in base:
                    base.append(kw)
        except Exception:
            pass
        return base

    def get_real_trend_signals(self, keywords: "list[str] | None" = None) -> dict:
        """
        Returns real Google Trends data for the given keywords (or the
        configured/default seed keywords):
          {
            "keywords": [...],
            "rising_queries": {keyword: [rising_query, ...], ...},
            "interest_snapshot": {keyword: latest_interest_value, ...},
          }

        Raises TrendDataFetchError if no real data could be retrieved after
        retries. Does not return partial fake data — either it's real or
        it's an exception.
        """
        kws = keywords or self._default_keywords()

        # D-1: serve from cache within TTL (trends barely move week-to-week).
        cached = self._load_cache(kws)
        if cached is not None:
            logger.info("TrendDataService: trend cache hit")
            return cached

        try:
            result = self._fetch_live(kws)
            self._save_cache(result)
            return result
        except TrendDataFetchError as e:
            # 1-6: a pytrends 429 ban would otherwise halt ALL product creation
            # for days with no alert. Serve the last cache even if expired (up to
            # a bounded staleness) rather than stop the factory — stale-real data
            # beats no data. Past the bound, fail loud AND alert once/day.
            stale = self._load_cache_stale(kws, max_days=7)
            if stale is not None:
                payload, age_hours = stale
                logger.warning(f"TrendDataService: serving STALE trend data ({age_hours:.0f}h old) after fetch failure: {e}")
                self._alert_stale(age_hours)
                payload = dict(payload)
                payload["stale"] = True
                payload["stale_hours"] = round(age_hours, 1)
                return payload
            self._alert_ban_once_per_day(str(e))
            raise

    def _fetch_live(self, kws) -> dict:
        rising_queries: dict = {}
        interest_snapshot: dict = {}
        interest_trend: dict = {}
        last_error = None

        for kw in kws:
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    # geo=US: Etsy buyers are predominantly US; worldwide dilutes.
                    self._pytrends.build_payload([kw], timeframe="today 3-m", geo="US")

                    interest_df = self._pytrends.interest_over_time()
                    if not interest_df.empty and kw in interest_df.columns:
                        now, prev, direction = self._series_direction(interest_df, kw)
                        interest_snapshot[kw] = int(now)
                        interest_trend[kw] = {"interest_now": now, "interest_prev": prev, "direction": direction}

                    related = self._pytrends.related_queries()
                    rising = related.get(kw, {}).get("rising")
                    if rising is not None and not rising.empty:
                        rising_queries[kw] = rising["query"].head(5).tolist()

                    break  # success for this keyword, move to next
                except Exception as e:
                    last_error = e
                    logger.warning(f"TrendDataService: attempt {attempt} failed for '{kw}': {e}")
                    if attempt < _MAX_RETRIES:
                        time.sleep(_BACKOFF_BASE_SECONDS * attempt + random.uniform(0, 2))

        if not rising_queries and not interest_snapshot:
            raise TrendDataFetchError(
                f"TrendDataService: could not retrieve any real trend data "
                f"for keywords={kws}. Last error: {last_error}"
            )

        # C-1: drop trademark/brand-poisoned rising queries before the prompt sees them.
        from app.core.trademark_screen import filter_queries
        rising_queries = {kw: filter_queries(qs) for kw, qs in rising_queries.items()}

        return {
            "keywords": kws,
            "rising_queries": rising_queries,
            "interest_snapshot": interest_snapshot,
            "interest_trend": interest_trend,  # 1-5: direction per keyword
        }

    @staticmethod
    def _series_direction(interest_df, kw):
        """1-5: a fading keyword must look different from a rising one. Drop the
        final PARTIAL week (Google understates it), then compare the mean of the
        last 4 full weeks vs the 4 before that. Returns (now, prev, direction)."""
        col = interest_df[kw]
        if "isPartial" in interest_df.columns:
            try:
                col = col[~interest_df["isPartial"].astype(bool)]
            except Exception:
                col = col.iloc[:-1] if len(col) > 1 else col
        elif len(col) > 1:
            col = col.iloc[:-1]  # drop likely-partial last bucket
        vals = [float(v) for v in col.tolist()]
        if not vals:
            return 0.0, 0.0, "flat"
        now = sum(vals[-4:]) / len(vals[-4:])
        prev_slice = vals[-8:-4] if len(vals) >= 8 else vals[:-4]
        prev = (sum(prev_slice) / len(prev_slice)) if prev_slice else now
        if prev <= 0:
            direction = "rising" if now > 0 else "flat"
        elif now >= prev * 1.2:
            direction = "rising"
        elif now <= prev * 0.8:
            direction = "falling"
        else:
            direction = "flat"
        return round(now, 1), round(prev, 1), direction

    # ── D-1: trend cache ────────────────────────────────────────────────────────

    def _cache_path(self):
        from app.core.paths import get_data_dir
        return get_data_dir() / "trend_cache.json"

    def _load_cache(self, kws):
        import json
        import time as _t
        ttl_hours = getattr(settings, "TREND_CACHE_HOURS", 12)
        if ttl_hours <= 0:
            return None
        try:
            p = self._cache_path()
            if not p.exists():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            if _t.time() - data.get("_cached_at", 0) > ttl_hours * 3600:
                return None
            # only serve cache if it was built for the same keyword set
            if data.get("payload", {}).get("keywords") != list(kws):
                return None
            return data["payload"]
        except Exception:
            return None

    def _load_cache_stale(self, kws, max_days: int = 7):
        """1-6: read the cached payload IGNORING the TTL, up to max_days old.
        Returns (payload, age_hours) or None."""
        import json
        import time as _t
        try:
            p = self._cache_path()
            if not p.exists():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("payload", {}).get("keywords") != list(kws):
                return None
            age_s = _t.time() - data.get("_cached_at", 0)
            if age_s > max_days * 86400:
                return None
            return data["payload"], age_s / 3600.0
        except Exception:
            return None

    def _save_cache(self, payload):
        import json
        import time as _t
        try:
            p = self._cache_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"_cached_at": _t.time(), "payload": payload}), encoding="utf-8")
        except Exception as e:
            logger.warning(f"TrendDataService: could not write trend cache: {e}")

    def _alert_stale(self, age_hours: float):
        try:
            from app.services.alert_service import AlertService
            AlertService().send_alert_sync(
                "Trend data is STALE",
                f"Google Trends fetch is failing; serving cached data {age_hours:.0f}h old. "
                "Likely a pytrends 429 ban. New products still generate off stale trends.",
                level="warning",
            )
        except Exception:
            pass

    def canary(self) -> dict:
        """5-3: weekly health check for the pytrends path. pytrends is an
        UNOFFICIAL scraper of an undocumented Google endpoint — a Google-side
        HTML/JSON change can silently break it (distinct from a transient 429).
        This does one tiny live fetch for a stable keyword and, on hard failure,
        alerts once so the breakage is caught proactively instead of only when
        product creation notices stale data. Returns a status dict."""
        kw = "wall art"
        try:
            self._pytrends.build_payload([kw], timeframe="today 3-m", geo="US")
            df = self._pytrends.interest_over_time()
            ok = df is not None and not df.empty and kw in getattr(df, "columns", [])
            if ok:
                return {"ok": True, "keyword": kw, "points": int(len(df))}
            self._alert_canary(f"pytrends returned no data for '{kw}' (empty/oddly-shaped frame)")
            return {"ok": False, "keyword": kw, "reason": "empty"}
        except Exception as e:
            self._alert_canary(f"pytrends raised {type(e).__name__}: {str(e)[:200]}")
            return {"ok": False, "keyword": kw, "reason": str(e)[:200]}

    def _alert_canary(self, detail: str):
        """5-3: alert at most once per day that the pytrends canary is failing."""
        import json
        import time as _t
        try:
            marker = self._cache_path().parent / "trend_canary_alert.json"
            last = 0
            if marker.exists():
                last = json.loads(marker.read_text(encoding="utf-8")).get("at", 0)
            if _t.time() - last < 86400:
                return
            from app.services.alert_service import AlertService
            AlertService().send_alert_sync(
                "Trend CANARY failing — pytrends may be broken",
                f"The weekly Google-Trends canary could not fetch data: {detail}. "
                "If this persists it is likely a pytrends library break (Google changed "
                "their endpoint), not a 429 — check for a pytrends update.",
                level="warning",
            )
            marker.write_text(json.dumps({"at": _t.time()}), encoding="utf-8")
        except Exception:
            pass

    def _alert_ban_once_per_day(self, err: str):
        """1-6: past the stale bound, the factory is halted — alert Maj at most
        once per day (today no alert fires at all)."""
        import json
        import time as _t
        try:
            marker = self._cache_path().parent / "trend_ban_alert.json"
            last = 0
            if marker.exists():
                last = json.loads(marker.read_text(encoding="utf-8")).get("at", 0)
            if _t.time() - last < 86400:
                return
            from app.services.alert_service import AlertService
            AlertService().send_alert_sync(
                "PRODUCT CREATION HALTED — no trend data",
                f"Google Trends has failed and the cache is too old to use. No new products "
                f"are being created. Likely a multi-day pytrends 429 ban. Error: {err[:200]}",
                level="error",
            )
            marker.write_text(json.dumps({"at": _t.time()}), encoding="utf-8")
        except Exception:
            pass
