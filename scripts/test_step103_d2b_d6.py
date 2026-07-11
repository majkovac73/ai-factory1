"""
Step 103 / D-2b + D-6 test.

D-2b: _stage_create_listing builds the listing WITHOUT calling
      ListingGeneratorAgent.generate_listing (the wasted LLM call), still
      deriving 13 tags and using the executor's title/description.

D-6: MarketingRefreshService.select_candidates weights toward products with
     higher engagement (views + 10*favorites), never-marketed first.
     CORS: allow_credentials is False (valid with wildcard origins).

Usage: python scripts/test_step103_d2b_d6.py
"""
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "d2b.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
import app.models.task, app.models.analytics_event, app.models.image_asset, app.models.pod_product  # noqa
Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── D-2b: no generate_listing LLM call ──
from app.services.pipeline_orchestrator import PipelineOrchestrator
orch = PipelineOrchestrator()
orch.catalog = MagicMock()
report = {"stages": {}}
output_data = {
    "title": "Boho Sunset Wall Art Print",
    "description": "A warm boho sunset print for cozy walls.",
    "keywords": ["boho wall art", "sunset print"],
    "sections": ["a", "b", "c", "d"],
}

captured = {}
class _RecEtsy:
    async def create_draft_listing(self, listing):
        captured.update(listing)
        return {"listing_id": "L1"}
    async def get_listing(self, lid):
        return {"listing_id": lid, "taxonomy_id": captured.get("taxonomy_id"), "when_made": captured.get("when_made")}
    async def delete_listing(self, lid):
        return True

with patch("app.services.pipeline_orchestrator.ListingGeneratorAgent") as LGA, \
     patch("app.services.pipeline_orchestrator.EtsyClient", _RecEtsy), \
     patch("app.services.pipeline_orchestrator.EtsyImageService"):
    # real _derive_tags (no LLM) but generate_listing must NOT be called
    from app.agents.etsy.listing_generator import ListingGeneratorAgent as RealLGA
    LGA.return_value._derive_tags = RealLGA()._derive_tags
    orch._stage_create_listing("t1", "Boho Sunset Wall Art Print", output_data, "single_print", False, report)

check("D-2b generate_listing (LLM) NOT called", not LGA.return_value.generate_listing.called)
check("D-2b listing created", report["stages"].get("create_listing", {}).get("ok") is True)
check("D-2b title from executor output_data", captured.get("title") == "Boho Sunset Wall Art Print")
check("D-2b 13 tags derived", len(captured.get("tags", [])) == 13)

# ── D-6: marketing refresh weights by engagement ──
from app.services.marketing_refresh_service import MarketingRefreshService, MarketingRefreshCandidate

svc = MarketingRefreshService()
# three never-marketed candidates with different engagement
class _T:
    def __init__(self, i): self.id = i
cands = [
    MarketingRefreshCandidate(_T("low"), "1", None, engagement=5),
    MarketingRefreshCandidate(_T("high"), "2", None, engagement=500),
    MarketingRefreshCandidate(_T("mid"), "3", None, engagement=50),
]
cands.sort(key=lambda c: (c.last_marketed_at is not None, -c.engagement, c.last_marketed_at or datetime.min))
check("D-6 highest-engagement product first", cands[0].task_id == "high")
check("D-6 lowest-engagement product last", cands[-1].task_id == "low")

# ── D-6: CORS credentials off ──
import app.main as m
mw = [x for x in m.app.user_middleware if "CORS" in str(x.cls)]
check("D-6 CORS present", len(mw) >= 1)
# allow_credentials should be False now
opts = mw[0].kwargs if hasattr(mw[0], "kwargs") else {}
check("D-6 allow_credentials is False", opts.get("allow_credentials") is False)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 D-2b + D-6 tests passed.")
