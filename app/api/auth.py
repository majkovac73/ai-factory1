"""
P0-3 API access control — pure, testable authorization logic for the
FACTORY_API_KEY gate applied by the HTTP middleware in app/main.py.

Kept separate from the middleware so it can be unit-tested without spinning
up the whole app (workers, DB migrations, provider registration).
"""
from config import settings

# External OAuth providers redirect the browser to these callbacks and cannot
# send our custom header — they must stay open even though they mutate state.
OPEN_CALLBACK_PREFIXES = (
    "/etsy/oauth/callback",
    "/tumblr/oauth/callback",
    "/pinterest/oauth/callback",
)

# Methods that spend money or mutate shop/marketing/analytics state.
_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def path_requires_key(path: str, method: str) -> bool:
    """
    True when this request must carry a valid X-Factory-Key (independent of
    whether a key is even configured). Protects every mutating request plus
    the sensitive /logs reads; leaves GET dashboards/health and OAuth
    callbacks open.
    """
    if any(path.startswith(p) for p in OPEN_CALLBACK_PREFIXES):
        return False
    if method.upper() in _MUTATING_METHODS:
        return True
    # /logs reads leak full prompts/outputs — protect them too.
    return path == "/logs" or path.startswith("/logs/")


def is_authorized(path: str, method: str, provided_key: str | None) -> bool:
    """
    Final allow/deny decision.
      - No FACTORY_API_KEY configured -> enforcement OFF (deploy-safe).
      - Request doesn't require a key -> allowed.
      - Otherwise the provided key must match exactly.
    """
    key = settings.FACTORY_API_KEY
    if not key:
        return True
    if not path_requires_key(path, method):
        return True
    return provided_key == key
