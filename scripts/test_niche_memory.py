"""
NicheMemoryService — the factory's persistent self-learning niche memory.

Verifies it (1) buckets products by niche, (2) classifies winners/losers from
REAL views/sales once there's enough traffic, (3) fails SAFE (no verdicts, empty
focus block) when traffic is too sparse to trust, and (4) persists across runs.

Usage: python scripts/test_niche_memory.py
"""
import os, sys, tempfile
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "nm.db")   # get_data_dir -> _tmp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.services.niche_memory_service import NicheMemoryService


class T:
    def __init__(self, id, type, keyword, occasion=None, title=""):
        self.id = id
        self.type = type
        self.metadata_ = {"occasion": occasion} if occasion else {}
        self.output_data = {"listing_id": "L" + id, "keywords": [keyword], "title": title}
        self.title = title


class Ev:
    def __init__(self, payload): self.payload = payload


class FakeAnalytics:
    def __init__(self, views_map): self.views_map = views_map
    def get_events(self, event_type, entity_type, entity_id, limit=1):
        if event_type == "listing_stats" and entity_id in self.views_map:
            v = self.views_map[entity_id]
            return [Ev({"views": v[0], "favorites": v[1]})]
        return []
    def record_event(self, *a, **k): pass


class FakeTS:
    def __init__(self, tasks): self.tasks = tasks
    def list_tasks(self): return self.tasks


class FakeRev:
    def __init__(self, rev): self.rev = rev
    def get_revenue_by_task(self): return self.rev


def run_update(tasks, views_map, rev):
    with patch("app.services.task_service.TaskService", return_value=FakeTS(tasks)), \
         patch("app.services.analytics_service.AnalyticsService", return_value=FakeAnalytics(views_map)), \
         patch("app.services.revenue_service.RevenueService", return_value=FakeRev(rev)):
        return NicheMemoryService().update(record=False)


# ── theme derivation ─────────────────────────────────────────────────────────
occ_task = T("x", "greeting_card_design", "christmas card", occasion="christmas")
kw_task = T("y", "pod_mug", "coffee brewing mug")
check("theme prefers occasion", NicheMemoryService.theme_of(occ_task) == "occasion:christmas")
check("theme falls back to primary keyword niche", NicheMemoryService.theme_of(kw_task) == "niche:coffee brewing mug")

# ── with real traffic: winners + losers classified ───────────────────────────
tasks = [
    T("c1", "pod_mug", "coffee mug"), T("c2", "pod_mug", "coffee mug"), T("c3", "pod_mug", "coffee mug"),  # coffee: high views
    T("w1", "single_print", "wedding sign"),                                                               # wedding: a SALE
    T("i1", "greeting_card_design", "ivf card"), T("i2", "greeting_card_design", "ivf card"),
    T("i3", "greeting_card_design", "ivf card"), T("i4", "greeting_card_design", "ivf card"),              # ivf: 4 listings, ~0 views
]
views = {"c1": (60, 2), "c2": (50, 1), "c3": (40, 0), "w1": (5, 0),
         "i1": (0, 0), "i2": (1, 0), "i3": (0, 0), "i4": (0, 0)}
rev = {"w1": 12.0}
mem = run_update(tasks, views, rev)
th = mem["themes"]
check("signal is trustworthy (156 views)", mem["signal_trustworthy"] is True)
check("coffee niche -> winner (high views)", th["niche:coffee mug"]["verdict"] == "winner")
check("wedding niche -> winner (earned money)", th["niche:wedding sign"]["verdict"] == "winner")
check("ivf niche -> loser (4 listings, ~0 views, $0)", th["niche:ivf"]["verdict"] == "loser")

fb = NicheMemoryService().focus_block()
check("focus block names a proven winner", "PROVEN WINNERS" in fb and ("coffee mug" in fb or "wedding sign" in fb))
check("focus block names the dead end", "DEAD ENDS" in fb and "ivf" in fb)

# ── persistence: survives a fresh instance ───────────────────────────────────
reloaded = NicheMemoryService().load()
check("memory persisted to disk", reloaded.get("signal_trustworthy") is True and "niche:coffee mug" in reloaded.get("themes", {}))

# ── fails SAFE with no traffic: no verdicts, empty focus block ────────────────
tasks2 = [T("a", "pod_mug", "coffee mug"), T("b", "single_print", "wedding sign")]
mem2 = run_update(tasks2, {"a": (1, 0), "b": (0, 0)}, {})
check("sparse traffic -> not trustworthy", mem2["signal_trustworthy"] is False)
check("sparse traffic -> no winners declared", all(v["verdict"] != "winner" for v in mem2["themes"].values()))
check("sparse traffic -> focus block is EMPTY (no overfitting)", NicheMemoryService().focus_block() == "")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All niche-memory tests passed.")
