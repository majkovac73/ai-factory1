"""
DEEP AUDIT V2 — unit tests for the code fixes.

Covers: #6 EtsyMarketService header, #11 backoff POST safety, #2 disabled formats +
PDF page cap, #1 LLM_MAX_TOKENS default, #10 IntelligenceAgent surfaces parse fail.

Usage: python scripts/test_deep_audit_v2_fixes.py
"""
import os, sys, asyncio
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# #6 — EtsyMarketService sends keystring:shared_secret
from app.services.etsy_market_service import EtsyMarketService
with patch.object(settings, "ETSY_API_KEY", "KEYSTRING"), patch.object(settings, "ETSY_SHARED_SECRET", "SECRET"):
    svc = EtsyMarketService()
    check("6 market header is keystring:shared_secret", svc._api_key == "KEYSTRING:SECRET")
with patch.object(settings, "ETSY_API_KEY", "KEYSTRING"), patch.object(settings, "ETSY_SHARED_SECRET", None):
    svc = EtsyMarketService()
    check("6 degrades (no crash) when secret missing", svc._api_key == "KEYSTRING")


# #11 — backoff: POST not retried on 5xx (only 429); GET retries 5xx
from app.core import http_backoff
from app.core.http_backoff import request_with_backoff

class _Resp:
    def __init__(s, code): s.status_code = code; s.headers = {"Retry-After": "0"}; s.reason_phrase = ""
class _Client:
    def __init__(s, seq): s.seq = list(seq); s.calls = 0
    async def _c(s):
        s.calls += 1
        return _Resp(s.seq.pop(0) if s.seq else 200)
    async def post(s, url, **k): return await s._c()
    async def get(s, url, **k): return await s._c()

async def _t():
    async def noop(*a, **k): return None
    with patch.object(http_backoff.asyncio, "sleep", new=noop):
        # POST 500 -> NOT retried (returns the 500 immediately)
        cp = _Client([500, 200]); rp = await request_with_backoff(cp, "POST", "u", max_retries=4)
        check("11 POST not retried on 500", cp.calls == 1 and rp.status_code == 500)
        # POST 429 -> retried
        cp2 = _Client([429, 200]); rp2 = await request_with_backoff(cp2, "POST", "u", max_retries=4)
        check("11 POST retried on 429", cp2.calls == 2 and rp2.status_code == 200)
        # GET 500 -> retried (idempotent)
        cg = _Client([500, 200]); rg = await request_with_backoff(cg, "GET", "u", max_retries=4)
        check("11 GET retried on 500", cg.calls == 2 and rg.status_code == 200)
asyncio.run(_t())


# #2 — seamless_pattern + phone_wallpaper excluded from proposable formats by default
from app.agents.trend_research_agent import TrendResearchAgent
with patch.object(settings, "SEAMLESS_PATTERN_ENABLED", False), \
     patch.object(settings, "PHONE_WALLPAPER_ENABLED", False), \
     patch.object(settings, "POD_APPAREL_ENABLED", False), \
     patch.object(settings, "WALL_ART_SET_ENABLED", False):
    fmts = TrendResearchAgent._proposable_formats()
    check("2 seamless_pattern disabled", "seamless_pattern" not in fmts)
    check("2 phone_wallpaper disabled", "phone_wallpaper" not in fmts)
    check("2 coloring_page still allowed", "coloring_page" in fmts)
with patch.object(settings, "SEAMLESS_PATTERN_ENABLED", True), \
     patch.object(settings, "PHONE_WALLPAPER_ENABLED", False):
    check("2 seamless re-enabled by flag", "seamless_pattern" in TrendResearchAgent._proposable_formats())


# #2 — PDF page cap clamps
from app.workers.autonomy_worker import AutonomyWorker
with patch.object(settings, "PDF_RELIABILITY_PAGE_CAP", 8):
    _, _, md = AutonomyWorker.build_task_from_concept(
        {"product_name": "P", "product_format": "pdf_planner_or_guide", "page_count": 24})
    check("2 page_count clamped to cap", md.get("page_count") == 8)
    _, _, md2 = AutonomyWorker.build_task_from_concept(
        {"product_name": "P", "product_format": "pdf_planner_or_guide", "page_count": 5})
    check("2 page_count under cap unchanged", md2.get("page_count") == 5)


# #1 — LLM_MAX_TOKENS default applied when max_tokens is None
from app.core.providers.openrouter_provider import OpenRouterProvider
prov = OpenRouterProvider.__new__(OpenRouterProvider)
captured = {}
class _Chat:
    class completions:
        @staticmethod
        async def create(**kw): captured.update(kw);
        # return a minimal response-like object
        # (defined below via monkeypatch)
class _Cli:
    chat = _Chat()
prov.client = _Cli()
class _Msg: content = "x"
class _Choice: message = _Msg()
class _R: choices = [_Choice()]; usage = None
async def _fake_create(**kw): captured.update(kw); return _R()
prov.client.chat.completions.create = _fake_create
with patch.object(settings, "LLM_MAX_TOKENS", 4000):
    asyncio.run(prov.generate(model="anthropic/claude-sonnet-5", prompt="hi"))
check("1 max_tokens defaulted to LLM_MAX_TOKENS", captured.get("max_tokens") == 4000)


# #10 — IntelligenceAgent surfaces parse failure (parse_failed flag + warning)
from app.agents.market_intelligence.intelligence import IntelligenceAgent
ia = IntelligenceAgent.__new__(IntelligenceAgent)
ia.sanitizer = MagicMock(); ia.sanitizer.extract.side_effect = ValueError("bad")
ia._generate = lambda p: "not json at all"
res = ia.synthesize("some research")
check("10 parse failure flagged", res.get("parse_failed") is True and res.get("opportunities") == [])

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All DEEP AUDIT V2 fix tests passed.")
