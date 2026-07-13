"""
Pinterest board helper + catalog backfill.

Usage: python scripts/test_pinterest_backfill.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pinbf.db")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.models.image_asset import ImageAsset
from app.models.marketing_post import MarketingPost
from app.models.pinterest_token import PinterestToken

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# ── /pinterest/boards helper endpoint ──
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)


async def fake_list_boards():
    return [{"id": "111", "name": "Printables", "privacy": "PUBLIC"},
            {"id": "222", "name": "Wall Art", "privacy": "PUBLIC"}]


with patch("app.api.routes.pinterest.pinterest_list_boards", side_effect=fake_list_boards):
    r = client.get("/pinterest/boards")
check("boards endpoint 200", r.status_code == 200)
body = r.json()
check("boards endpoint returns ids + names", body["count"] == 2 and body["boards"][0]["id"] == "111")

# not connected -> 400 with a helpful message
async def boom():
    raise ValueError("No Pinterest token found")
with patch("app.api.routes.pinterest.pinterest_list_boards", side_effect=boom):
    r2 = client.get("/pinterest/boards")
check("boards endpoint 400 when not connected", r2.status_code == 400)

# ── backfill candidate selection ──
from app.services.pinterest_backfill_service import PinterestBackfillService

db = SessionLocal()
# published product (has listing_id via ImageAsset), never pinned
db.add(Task(id="pub1", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={"title": "Boho Sunset Print"}))
db.add(ImageAsset(task_id="pub1", variant="listing", use_case="listing", agent="x",
                  local_path="/tmp/pub1_hero.png", listing_id="L1"))
# published product already pinned -> excluded
db.add(Task(id="pub2", prompt="p", type="coloring_page", status="DONE", input_data={},
            output_data={"title": "Cat Coloring Page"}))
db.add(ImageAsset(task_id="pub2", variant="listing", use_case="listing", agent="x",
                  local_path="/tmp/pub2_hero.png", listing_id="L2"))
db.add(MarketingPost(task_id="pub2", channel="pinterest", status="success", external_id="pinX"))
# unpublished (no listing_id) -> excluded
db.add(Task(id="draft", prompt="p", type="single_print", status="DONE", input_data={},
            output_data={"title": "Draft Only"}))
db.commit(); db.close()

svc = PinterestBackfillService()
cands = svc.candidates()
ids = {c["task_id"] for c in cands}
check("backfill includes the never-pinned published product", "pub1" in ids)
check("backfill EXCLUDES the already-pinned product", "pub2" not in ids)
check("backfill EXCLUDES unpublished (no listing_id)", "draft" not in ids)
check("backfill includes already-pinned when asked",
      "pub2" in {c["task_id"] for c in svc.candidates(include_already_pinned=True)})

# ── dry run posts nothing ──
rep = svc.run(apply=False, limit=50)
check("dry run reports the plan, posts nothing", rep["applied"] is False and rep["posted"] == 0 and rep["to_post"] == 1)

# ── apply posts via the refresh path, respects limit + records success ──
posts = []


def fake_refresh_post(task_id, channel, listing_id=None, rewrite_caption=True):
    posts.append(task_id)
    return {"success": True, "external_id": "pin1", "url": "https://pin/1", "error": None}


with patch.object(svc.refresh, "refresh_post", side_effect=fake_refresh_post), \
     patch("app.services.pinterest_oauth.is_connected", return_value=True):
    rep2 = svc.run(apply=True, limit=50, sleep_seconds=0, rewrite_caption=False)
check("apply posted the candidate", rep2["posted"] == 1 and posts == ["pub1"])
check("apply result records the url", rep2["results"][0]["url"] == "https://pin/1")

# apply refuses when not connected
with patch("app.services.pinterest_oauth.is_connected", return_value=False):
    rep3 = svc.run(apply=True, limit=50, sleep_seconds=0)
check("apply refuses when Pinterest not connected", "not connected" in (rep3.get("error") or ""))

# limit is respected
db = SessionLocal()
for i in range(5):
    tid = f"more{i}"
    db.add(Task(id=tid, prompt="p", type="single_print", status="DONE", input_data={}, output_data={"title": f"P{i}"}))
    db.add(ImageAsset(task_id=tid, variant="listing", use_case="listing", agent="x",
                      local_path=f"/tmp/{tid}.png", listing_id=f"LL{i}"))
db.commit(); db.close()
posts.clear()
with patch.object(svc.refresh, "refresh_post", side_effect=fake_refresh_post), \
     patch("app.services.pinterest_oauth.is_connected", return_value=True):
    rep4 = svc.run(apply=True, limit=3, sleep_seconds=0, rewrite_caption=False)
check("limit caps the number of posts", rep4["posted"] == 3 and len(posts) == 3)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All Pinterest backfill tests passed.")
