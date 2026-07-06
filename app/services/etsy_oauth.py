import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta

import httpx

from app.db.database import SessionLocal
from app.models.etsy_token import EtsyToken
from config import settings

ETSY_AUTH_URL = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"

# In-memory PKCE verifier storage, keyed by "state". Fine for a single-
# operator local app; would need a real store (DB/session) for
# multi-user deployments.
_pending_verifiers = {}


def _generate_pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(40)).rstrip(b"=").decode("utf-8")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).rstrip(b"=").decode("utf-8")
    return verifier, challenge


def build_authorization_url(scopes: str = "listings_r listings_w shops_r shops_w") -> str:
    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    _pending_verifiers[state] = verifier

    params = {
        "response_type": "code",
        "client_id": settings.ETSY_API_KEY,
        "redirect_uri": settings.ETSY_REDIRECT_URI,
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    return f"{ETSY_AUTH_URL}?{query}"


async def exchange_code_for_token(code: str, state: str) -> dict:
    verifier = _pending_verifiers.pop(state, None)
    if not verifier:
        raise ValueError("Unknown or expired OAuth state")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            ETSY_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": settings.ETSY_API_KEY,
                "redirect_uri": settings.ETSY_REDIRECT_URI,
                "code": code,
                "code_verifier": verifier,
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
        existing = db.query(EtsyToken).filter(EtsyToken.shop_id == settings.ETSY_SHOP_ID).first()

        if existing:
            existing.access_token = token_data["access_token"]
            existing.refresh_token = token_data["refresh_token"]
            existing.expires_at = expires_at
        else:
            existing = EtsyToken(
                shop_id=settings.ETSY_SHOP_ID,
                access_token=token_data["access_token"],
                refresh_token=token_data["refresh_token"],
                expires_at=expires_at,
            )
            db.add(existing)

        db.commit()
    finally:
        db.close()


async def get_valid_access_token() -> str:
    db = SessionLocal()
    try:
        token = db.query(EtsyToken).filter(EtsyToken.shop_id == settings.ETSY_SHOP_ID).first()
        if not token:
            raise ValueError("No Etsy token found — complete the OAuth flow first via /etsy/oauth/login")

        if token.expires_at <= datetime.utcnow() + timedelta(seconds=60):
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    ETSY_TOKEN_URL,
                    json={
                        "grant_type": "refresh_token",
                        "client_id": settings.ETSY_API_KEY,
                        "refresh_token": token.refresh_token,
                    },
                )
                response.raise_for_status()
                new_data = response.json()

            token.access_token = new_data["access_token"]
            token.refresh_token = new_data["refresh_token"]
            token.expires_at = datetime.utcnow() + timedelta(seconds=new_data.get("expires_in", 3600))
            db.commit()

        return token.access_token
    finally:
        db.close()