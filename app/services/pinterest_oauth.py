import base64
import secrets
from datetime import datetime, timedelta

import httpx

from app.db.database import SessionLocal
from app.models.pinterest_token import PinterestToken
from config import settings

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


async def get_valid_access_token() -> str:
    db = SessionLocal()
    try:
        token = db.query(PinterestToken).first()
        if not token:
            raise ValueError("No Pinterest token found — complete OAuth via /pinterest/oauth/login")

        if token.expires_at <= datetime.utcnow() + timedelta(seconds=60):
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