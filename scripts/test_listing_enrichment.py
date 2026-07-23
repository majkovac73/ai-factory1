"""
ListingEnrichmentService — rewrite thin descriptions to full length + push to Etsy.

Usage: python scripts/test_listing_enrichment.py
"""
import os, sys, tempfile
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "enr.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.services.listing_enrichment_service import ListingEnrichmentService


class FakeGen:
    """Returns a short body first, then a longer one on the 'too short' retry."""
    def __init__(self, short=False):
        self.calls = 0
        self.short = short
    def _generate(self, prompt, temperature=None):
        self.calls += 1
        if "too short" in prompt or not self.short or self.calls > 1:
            return "This is a full, benefit-led description. " * 20  # ~800 chars
        return "Too short."


# ── build_description: short body triggers a retry, blocks + disclosure appended ─
with patch.object(__import__("config").settings, "SHOP_AI_DISCLOSURE", "AI-assisted design disclosure."):
    fg = FakeGen(short=True)
    svc = ListingEnrichmentService(generator=fg)
    desc = svc.build_description("Coffee Lover Mug", "pod_mug", ["coffee mug", "barista gift"])
check("build: retries when body is short", fg.calls == 2)
check("build: reaches full length", len(desc) >= 700)
check("build: appends WHAT YOU GET block", "WHAT YOU GET" in desc)
check("build: appends AI disclosure", "disclosure" in desc.lower())

# digital vs POD medium wording is chosen from the format
captured = {}
class CapGen:
    def _generate(self, prompt, temperature=None):
        captured["prompt"] = prompt
        return "A complete description body. " * 30
ListingEnrichmentService(generator=CapGen()).build_description("Budget Planner", "pdf_planner_or_guide", ["budget planner"])
check("build: digital product -> instant download wording", "INSTANT DIGITAL DOWNLOAD" in captured["prompt"])

# ── enrich_all: only thin listings, PATCHes Etsy, respects apply flag ───────────
class T:
    def __init__(self, id, desc, listing=True):
        self.id = id; self.type = "greeting_card_design"
        self.metadata_ = {}
        self.output_data = {"title": "Card " + id, "keywords": ["greeting card"],
                            "description": desc, "listing_id": ("L" + id) if listing else None}

tasks = [
    T("thin", "short desc"),                       # thin -> rewrite
    T("rich", "x" * 900),                          # already rich -> skip
    T("draft", "short", listing=False),            # not published -> skip
]
patched = {"n": 0}
async def fake_patch(self, listing_id, description):
    patched["n"] += 1
    patched["listing_id"] = listing_id
    patched["len"] = len(description)

with patch("app.services.task_service.TaskService") as TS, \
     patch.object(ListingEnrichmentService, "_patch", fake_patch), \
     patch.object(ListingEnrichmentService, "_persist_description", lambda *a, **k: None):
    TS.return_value.list_tasks.return_value = tasks
    rep = ListingEnrichmentService(generator=FakeGen()).enrich_all(apply=True, min_chars=650)

check("enrich_all: only the thin published listing is rewritten", rep["to_enrich"] == 1)
check("enrich_all: PATCHed Etsy exactly once", patched["n"] == 1 and patched.get("listing_id") == "Lthin")
check("enrich_all: pushed a long description", patched.get("len", 0) >= 700)
check("enrich_all: reports one enriched", rep["enriched"] == 1)

# dry-run applies nothing
patched["n"] = 0
with patch("app.services.task_service.TaskService") as TS, \
     patch.object(ListingEnrichmentService, "_patch", fake_patch):
    TS.return_value.list_tasks.return_value = tasks
    rep2 = ListingEnrichmentService(generator=FakeGen()).enrich_all(apply=False, min_chars=650)
check("enrich_all: dry-run PATCHes nothing", patched["n"] == 0 and rep2["enriched"] == 0)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All listing-enrichment tests passed.")
