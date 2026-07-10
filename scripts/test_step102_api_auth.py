"""
Step 102 / P0-3 test — FACTORY_API_KEY gate (app/api/auth.py).

Covers: enforcement off when unset; mutating methods require the key; /logs
reads require the key; GET dashboards/health stay open; OAuth callbacks stay
open even though they mutate; wrong/missing key -> denied; correct key -> allowed.

Usage: python scripts/test_step102_api_auth.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from app.api import auth

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# --- enforcement OFF when no key configured ---
settings.FACTORY_API_KEY = None
check("no key -> POST allowed (enforcement off)", auth.is_authorized("/tasks", "POST", None))
check("no key -> /logs allowed", auth.is_authorized("/logs", "GET", None))

# --- enforcement ON ---
settings.FACTORY_API_KEY = "secret123"

# mutating methods need the key
check("POST without key -> denied", not auth.is_authorized("/tasks", "POST", None))
check("POST wrong key -> denied", not auth.is_authorized("/tasks", "POST", "nope"))
check("POST correct key -> allowed", auth.is_authorized("/tasks", "POST", "secret123"))
for m in ("PUT", "PATCH", "DELETE"):
    check(f"{m} without key -> denied", not auth.is_authorized("/etsy/listings/1", m, None))

# /logs reads are protected even as GET
check("GET /logs without key -> denied", not auth.is_authorized("/logs", "GET", None))
check("GET /logs/5 without key -> denied", not auth.is_authorized("/logs/5", "GET", None))
check("GET /logs with key -> allowed", auth.is_authorized("/logs", "GET", "secret123"))

# read-only dashboards/health stay open without a key
check("GET /dashboard open", auth.is_authorized("/dashboard", "GET", None))
check("GET /dashboard/rooms/status open", auth.is_authorized("/dashboard/rooms/status", "GET", None))
check("GET /health open", auth.is_authorized("/health", "GET", None))
check("GET /tasks (read) open", auth.is_authorized("/tasks", "GET", None))

# OAuth callbacks stay open even for GET/POST (external redirect can't send header)
check("GET etsy oauth callback open", auth.is_authorized("/etsy/oauth/callback", "GET", None))
check("GET tumblr oauth callback open", auth.is_authorized("/tumblr/oauth/callback", "GET", None))
check("GET pinterest oauth callback open", auth.is_authorized("/pinterest/oauth/callback", "GET", None))
# ...but non-callback etsy mutations are still protected
check("POST /etsy/listings/upload denied without key",
      not auth.is_authorized("/etsy/listings/upload", "POST", None))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 API-auth tests passed.")
