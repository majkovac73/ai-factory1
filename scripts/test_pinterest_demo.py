"""
Pinterest demo script wiring + /pinterest/account endpoint.

Usage: python scripts/test_pinterest_demo.py
"""
import importlib.util
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pdemo.db")
os.environ.setdefault("IMAGE_STORAGE_ROOT", os.path.join(tempfile.mkdtemp(), "images"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.models.pinterest_token import PinterestToken  # noqa
from app.models.marketing_post import MarketingPost  # noqa
from app.models.task import Task  # noqa
from config import settings

Base.metadata.create_all(bind=engine)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# load the demo module
spec = importlib.util.spec_from_file_location(
    "pdemo", os.path.join(os.path.dirname(__file__), "pinterest_demo.py"))
pdemo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pdemo)

# ── /pinterest/account endpoint ──
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)


async def fake_account():
    return {"username": "majkovacai", "business_name": "DesignsForAll", "id": "123",
            "account_type": "BUSINESS", "board_count": 2}


with patch("app.api.routes.pinterest.pinterest_get_user_account", side_effect=fake_account):
    r = client.get("/pinterest/account")
check("/pinterest/account 200", r.status_code == 200)
check("/pinterest/account returns the profile", r.json().get("username") == "majkovacai")


async def boom():
    raise ValueError("No Pinterest token found")
with patch("app.api.routes.pinterest.pinterest_get_user_account", side_effect=boom):
    r2 = client.get("/pinterest/account")
check("/pinterest/account 400 when not connected", r2.status_code == 400)

# ── phase2 exercises the REAL feature path (account -> boards -> publish pin) ──
posted = {}


class FakeChannelResult:
    pass


def fake_refresh_post(task_id, channel, listing_id=None, rewrite_caption=True):
    posted.update(task_id=task_id, listing_id=listing_id, channel=type(channel).__name__)
    return {"success": True, "external_id": "pin123", "url": "https://www.pinterest.com/pin/pin123/", "error": None}


boards = [{"name": "Printables", "id": "111", "privacy": "PUBLIC"}]

async def fake_get_pin(pin_id):
    return {"id": pin_id, "board_id": "111", "title": "Boho Sunset Print"}

with patch.object(pdemo.pinterest_oauth, "get_user_account", side_effect=fake_account), \
     patch.object(pdemo.pinterest_oauth, "list_boards", side_effect=lambda: boards), \
     patch.object(pdemo.pinterest_oauth, "get_pin", side_effect=fake_get_pin), \
     patch.object(pdemo.PinterestBackfillService, "candidates",
                  return_value=[{"task_id": "t1", "listing_id": "L1", "title": "Boho Sunset Print"}]), \
     patch.object(pdemo.MarketingRefreshService, "_pick_asset_path", return_value="/tmp/hero.png"), \
     patch.object(pdemo.MarketingRefreshService, "refresh_post", side_effect=fake_refresh_post), \
     patch("builtins.input", return_value=""), \
     patch("webbrowser.open", return_value=True):
    pdemo.phase2_core_features(boards)

check("phase2 published a real pin via refresh_post (production path)", posted.get("task_id") == "t1")
check("phase2 used the PinterestChannel", posted.get("channel") == "PinterestChannel")
check("phase2 linked the real listing", posted.get("listing_id") == "L1")

# ── sandbox routing: api_base/token_url flip; sandbox-token phase1 skips OAuth ──
from app.services import pinterest_oauth as po
with patch.object(settings, "PINTEREST_SANDBOX", True):
    check("sandbox api_base -> sandbox host", po.api_base() == "https://api-sandbox.pinterest.com/v5")
    check("sandbox token_url -> sandbox host", po.token_url() == "https://api-sandbox.pinterest.com/v5/oauth/token")
with patch.object(settings, "PINTEREST_SANDBOX", False):
    check("production api_base -> prod host", po.api_base() == "https://api.pinterest.com/v5")

# sandbox token bypasses OAuth/DB in get_valid_access_token
import asyncio as _aio
with patch.object(settings, "PINTEREST_SANDBOX", True), patch.object(settings, "PINTEREST_SANDBOX_TOKEN", "sbx_tok_123"):
    tok = _aio.run(po.get_valid_access_token())
check("sandbox token used directly (no OAuth needed)", tok == "sbx_tok_123")

# phase1 in sandbox-token mode skips the browser and confirms via account read
httpx_called = {"n": 0}
def _should_not_call(*a, **k):
    httpx_called["n"] += 1
    raise AssertionError("phase1 should NOT hit /oauth/login in sandbox-token mode")
with patch.object(settings, "PINTEREST_SANDBOX", True), patch.object(settings, "PINTEREST_SANDBOX_TOKEN", "sbx_tok_123"), \
     patch.object(pdemo.pinterest_oauth, "get_user_account", side_effect=fake_account), \
     patch("httpx.get", side_effect=_should_not_call):
    out = pdemo.phase1_authenticate("http://localhost:8000")
check("sandbox-token phase1 skips browser OAuth", httpx_called["n"] == 0 and out is None)

# ── phase1 pulls the auth URL from the RUNNING app (state correctness) ──
seen = {}


class _Resp:
    status_code = 200

    def raise_for_status(self): pass

    @staticmethod
    def json():
        return {"authorization_url": "https://www.pinterest.com/oauth/?client_id=1"}


def fake_get(url, timeout=30):
    seen["login_url"] = url
    return _Resp()


with patch("httpx.get", side_effect=fake_get), \
     patch.object(pdemo.pinterest_oauth, "list_boards", side_effect=lambda: boards), \
     patch("builtins.input", return_value=""), \
     patch("webbrowser.open", return_value=True):
    out_boards = pdemo.phase1_authenticate("http://localhost:8000")
check("phase1 fetches auth URL from the running app's /pinterest/oauth/login",
      seen.get("login_url") == "http://localhost:8000/pinterest/oauth/login")
check("phase1 confirms the token by reading real boards", out_boards == boards)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All Pinterest demo tests passed.")
