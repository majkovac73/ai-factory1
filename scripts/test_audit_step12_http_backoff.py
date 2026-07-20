"""
Audit 2026-07-20 #12 — HTTP retry/backoff on 429/5xx.

request_with_backoff must: retry on 429/5xx, honor Retry-After, stop at
max_retries, and return the final response. Uses a fake client (no network, no
real sleeping — asyncio.sleep is patched).

Usage: python scripts/test_audit_step12_http_backoff.py
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from app.core import http_backoff
from app.core.http_backoff import request_with_backoff, _parse_retry_after

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


class FakeResp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class FakeClient:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def get(self, url, **kwargs):
        self.calls += 1
        s = self.statuses.pop(0) if self.statuses else 200
        return FakeResp(s, {"Retry-After": "0"})


async def _noop_sleep(*_a, **_k):
    return None


async def main():
    with patch.object(http_backoff.asyncio, "sleep", new=_noop_sleep):
        # retries through 429s then succeeds
        c = FakeClient([429, 429, 200])
        r = await request_with_backoff(c, "GET", "http://x", max_retries=4)
        check("retries 429 then returns 200", r.status_code == 200 and c.calls == 3)

        # gives up after max_retries and returns the last failure
        c2 = FakeClient([429, 429, 429, 429, 429, 429])
        r2 = await request_with_backoff(c2, "GET", "http://x", max_retries=2)
        check("stops at max_retries (1 initial + 2 retries = 3 calls)", c2.calls == 3 and r2.status_code == 429)

        # non-retry status returns immediately
        c3 = FakeClient([404])
        r3 = await request_with_backoff(c3, "GET", "http://x", max_retries=4)
        check("404 not retried", c3.calls == 1 and r3.status_code == 404)

        # 500 is retried
        c4 = FakeClient([500, 200])
        r4 = await request_with_backoff(c4, "GET", "http://x", max_retries=4)
        check("5xx retried", c4.calls == 2 and r4.status_code == 200)


asyncio.run(main())

check("Retry-After seconds parsed", _parse_retry_after("12") == 12.0)
check("Retry-After junk -> None", _parse_retry_after("not-a-date") is None)
check("Retry-After empty -> None", _parse_retry_after(None) is None)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#12 backoff tests passed.")
