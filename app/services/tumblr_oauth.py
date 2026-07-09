"""
Tumblr OAuth 2.0 (Authorization Code + Refresh Token grant).

Confirmed against Tumblr's current API docs (github.com/tumblr/docs api.md,
July 2026): Tumblr moved to OAuth 2.0 (not the old OAuth 1.0a).
  - Authorize:  https://www.tumblr.com/oauth2/authorize
  - Token:      https://api.tumblr.com/v2/oauth2/token
  - Scopes:     "basic write offline_access" (offline_access → refresh_token)
  - Client creds (the app's TUMBLR_CONSUMER_KEY / TUMBLR_CONSUMER_SECRET) are
    sent as client_id / client_secret in the token request body (form-encoded).

Token storage mirrors PinterestToken exactly (single-row table, refresh on
expiry). Access via /tumblr/oauth/login → /tumblr/oauth/callback.
"""
import secrets
from datetime import datetime, timedelta

import httpx

from app.db.database import SessionLocal
from app.models.tumblr_token import TumblrToken
from config import settings

TUMBLR_AUTH_URL = "https://www.tumblr.com/oauth2/authorize"
TUMBLR_TOKEN_URL = "https://api.tumblr.com/v2/oauth2/token"
TUMBLR_SCOPES = "basic write offline_access"

_pending_states = set()


def build_authorization_url(scopes: str = TUMBLR_SCOPES) -> str:
    if not settings.TUMBLR_CONSUMER_KEY:
        raise ValueError("TUMBLR_CONSUMER_KEY is not set")
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)

    params = {
        "client_id": settings.TUMBLR_CONSUMER_KEY,
        "response_type": "code",
        "scope": scopes,
        "state": state,
    }
    # redirect_uri is OPTIONAL per Tumblr's docs when exactly one callback URL
    # is registered on the app — Tumblr then falls back to that registered
    # default. We omit it deliberately: sending it triggered a persistent
    # redirect_uri_mismatch even with an exactly-matching registration, and
    # omitting sidesteps any exact-string comparison quirk. Only include it if
    # explicitly opted in (needed only if multiple callback URLs are registered).
    if getattr(settings, "TUMBLR_SEND_REDIRECT_URI", False):
        params["redirect_uri"] = settings.TUMBLR_REDIRECT_URI
    # httpx encodes spaces in scope, etc.
    query = str(httpx.QueryParams(params))
    return f"{TUMBLR_AUTH_URL}?{query}"


async def exchange_code_for_token(code: str, state: str) -> dict:
    if state not in _pending_states:
        raise ValueError("Unknown or expired OAuth state")
    _pending_states.discard(state)

    token_body = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.TUMBLR_CONSUMER_KEY,
        "client_secret": settings.TUMBLR_CONSUMER_SECRET,
    }
    # Must match the authorize request: only send redirect_uri in the token
    # exchange if we sent it in the authorize step (see build_authorization_url).
    if getattr(settings, "TUMBLR_SEND_REDIRECT_URI", False):
        token_body["redirect_uri"] = settings.TUMBLR_REDIRECT_URI

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TUMBLR_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=token_body,
        )
        response.raise_for_status()
        token_data = response.json()

    save_token(token_data)
    return token_data


def save_token(token_data: dict):
    db = SessionLocal()
    try:
        expires_at = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600))
        existing = db.query(TumblrToken).first()

        if existing:
            existing.access_token = token_data["access_token"]
            existing.refresh_token = token_data.get("refresh_token", existing.refresh_token)
            existing.expires_at = expires_at
        else:
            existing = TumblrToken(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", ""),
                expires_at=expires_at,
            )
            db.add(existing)

        db.commit()
    finally:
        db.close()


async def get_valid_access_token() -> str:
    db = SessionLocal()
    try:
        token = db.query(TumblrToken).first()
        if not token:
            raise ValueError("No Tumblr token found — complete OAuth via /tumblr/oauth/login")

        if token.expires_at <= datetime.utcnow() + timedelta(seconds=60):
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    TUMBLR_TOKEN_URL,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": token.refresh_token,
                        "client_id": settings.TUMBLR_CONSUMER_KEY,
                        "client_secret": settings.TUMBLR_CONSUMER_SECRET,
                    },
                )
                response.raise_for_status()
                new_data = response.json()

            token.access_token = new_data["access_token"]
            token.refresh_token = new_data.get("refresh_token", token.refresh_token)
            token.expires_at = datetime.utcnow() + timedelta(seconds=new_data.get("expires_in", 3600))
            db.commit()

        return token.access_token
    finally:
        db.close()
