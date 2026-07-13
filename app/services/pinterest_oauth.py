import base64
import secrets
import threading
from datetime import datetime, timedelta

import httpx

from app.db.database import SessionLocal
from app.models.pinterest_token import PinterestToken
from config import settings

# P0-10: serialize concurrent refreshes across threads (single-row, rotating
# refresh token — same rationale as etsy_oauth._refresh_lock).
_refresh_lock = threading.Lock()

PINTEREST_AUTH_URL = "https://www.pinterest.com/oauth"
PINTEREST_TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"

_pending_states = set()


def is_connected() -> bool:
    """
    Cheap, synchronous check for whether Pinterest can actually receive a
    post: app credentials + board configured AND an OAuth token row exists.
    Used by the pipeline to skip the (billable) pin-image generation entirely
    when Pinterest isn't connected — see P0-6. Does NOT refresh the token.
    """
    if not (settings.PINTEREST_APP_ID and settings.PINTEREST_APP_SECRET and settings.PINTEREST_BOARD_ID):
        return False
    db = SessionLocal()
    try:
        return db.query(PinterestToken).first() is not None
    finally:
        db.close()


def disconnect() -> dict:
    """Disconnect the Pinterest account and delete ALL Pinterest-derived data
    from our systems, immediately. This backs the privacy-policy promise (see
    /privacy): on disconnect we stop accessing Pinterest and purge what we stored.

    Deletes:
      - the stored OAuth token(s) (access + refresh) — after this we can no longer
        call the Pinterest API for the account;
      - every MarketingPost we recorded for the Pinterest channel (the only other
        Pinterest-derived data the app persists — Pin ids/urls/payloads).
    Returns a count of what was removed. Idempotent (safe to call when already
    disconnected)."""
    from app.models.marketing_post import MarketingPost
    db = SessionLocal()
    try:
        tokens = db.query(PinterestToken).delete()
        posts = db.query(MarketingPost).filter(MarketingPost.channel == "pinterest").delete()
        db.commit()
        return {"disconnected": True, "tokens_deleted": int(tokens), "pinterest_posts_deleted": int(posts)}
    finally:
        db.close()


def build_authorization_url(scopes: str = "boards:read,pins:read,pins:write") -> str:
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)

    params = {
        "response_type": "code",
        "client_id": settings.PINTEREST_APP_ID,
        "redirect_uri": settings.PINTEREST_REDIRECT_URI,
        "scope": scopes,
        "state": state,
    }
    query = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    return f"{PINTEREST_AUTH_URL}/?{query}"


async def exchange_code_for_token(code: str, state: str) -> dict:
    if state not in _pending_states:
        raise ValueError("Unknown or expired OAuth state")
    _pending_states.discard(state)

    credentials = base64.b64encode(
        f"{settings.PINTEREST_APP_ID}:{settings.PINTEREST_APP_SECRET}".encode("utf-8")
    ).decode("utf-8")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            PINTEREST_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.PINTEREST_REDIRECT_URI,
            },
        )
        response.raise_for_status()
        token_data = response.json()

    save_token(token_data)
    return token_data


def save_token(token_data: dict):
    db = SessionLocal()
    try:
        expires_at = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
        existing = db.query(PinterestToken).first()

        if existing:
            existing.access_token = token_data["access_token"]
            existing.refresh_token = token_data.get("refresh_token", existing.refresh_token)
            existing.expires_at = expires_at
        else:
            existing = PinterestToken(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", ""),
                expires_at=expires_at,
            )
            db.add(existing)

        db.commit()
    finally:
        db.close()


def _needs_refresh(token) -> bool:
    return token.expires_at <= datetime.utcnow() + timedelta(seconds=60)


async def get_valid_access_token() -> str:
    # Fast path: valid token, no lock needed.
    db = SessionLocal()
    try:
        token = db.query(PinterestToken).first()
        if not token:
            raise ValueError("No Pinterest token found — complete OAuth via /pinterest/oauth/login")
        if not _needs_refresh(token):
            return token.access_token
    finally:
        db.close()

    # Slow path: serialize refresh; re-read under the lock so a token another
    # thread just rotated is reused instead of refreshed again.
    _refresh_lock.acquire()
    try:
        db = SessionLocal()
        try:
            token = db.query(PinterestToken).first()
            if not token:
                raise ValueError("No Pinterest token found — complete OAuth via /pinterest/oauth/login")
            if not _needs_refresh(token):
                return token.access_token

            credentials = base64.b64encode(
                f"{settings.PINTEREST_APP_ID}:{settings.PINTEREST_APP_SECRET}".encode("utf-8")
            ).decode("utf-8")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    PINTEREST_TOKEN_URL,
                    headers={
                        "Authorization": f"Basic {credentials}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": token.refresh_token,
                    },
                )
                response.raise_for_status()
                new_data = response.json()

            token.access_token = new_data["access_token"]
            token.expires_at = datetime.utcnow() + timedelta(seconds=new_data.get("expires_in", 3600))
            db.commit()

            return token.access_token
        finally:
            db.close()
    finally:
        _refresh_lock.release()