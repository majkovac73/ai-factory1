"""
Step 102 / P0-10 test — Etsy OAuth refresh is serialized across threads.

Etsy rotates the refresh token on every refresh; two threads refreshing
concurrently with the same old token can invalidate the whole family. This
test points the app at a throwaway SQLite DB, seeds an EXPIRED token, fires 10
threads that each call get_valid_access_token(), and asserts the refresh
endpoint was hit EXACTLY ONCE (the other 9 threads re-read the freshly rotated
token under the lock).

Usage: python scripts/test_step102_oauth_refresh_lock.py
"""
import asyncio
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta

# Point the app at a temp DB BEFORE importing any app module that binds the engine.
_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_tmpdir, "oauth_test.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from app.db.database import Base, engine, SessionLocal
from app.models.etsy_token import EtsyToken
from config import settings
import app.services.etsy_oauth as etsy_oauth

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


settings.ETSY_SHOP_ID = "test-shop"
settings.ETSY_API_KEY = "test-key"

Base.metadata.create_all(bind=engine)

# Seed an already-expired token.
db = SessionLocal()
db.query(EtsyToken).delete()
db.add(EtsyToken(
    shop_id="test-shop",
    access_token="old-access",
    refresh_token="old-refresh-0",
    expires_at=datetime.utcnow() - timedelta(seconds=10),
))
db.commit()
db.close()

# Fake httpx client counting refresh POSTs and rotating the token each time.
_refresh_calls = 0
_calls_lock = threading.Lock()


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kw):
        global _refresh_calls
        with _calls_lock:
            _refresh_calls += 1
            n = _refresh_calls
        # small delay to widen the race window
        await asyncio.sleep(0.05)
        return _FakeResponse({
            "access_token": f"new-access-{n}",
            "refresh_token": f"new-refresh-{n}",
            "expires_in": 3600,
        })


results = []
results_lock = threading.Lock()


def worker():
    with patch.object(etsy_oauth.httpx, "AsyncClient", _FakeAsyncClient):
        token = asyncio.run(etsy_oauth.get_valid_access_token())
    with results_lock:
        results.append(token)


threads = [threading.Thread(target=worker) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("exactly ONE refresh HTTP call across 10 threads", _refresh_calls == 1)
check("all 10 threads returned a token", len(results) == 10)
check("all threads returned the SAME refreshed access token", len(set(results)) == 1)
check("returned token is the refreshed one", results and results[0] == "new-access-1")

# Second call now that token is valid -> no additional refresh.
with patch.object(etsy_oauth.httpx, "AsyncClient", _FakeAsyncClient):
    asyncio.run(etsy_oauth.get_valid_access_token())
check("valid token -> no extra refresh (fast path)", _refresh_calls == 1)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 OAuth-refresh-lock tests passed.")
