"""
Shared HTTP retry/backoff for external APIs (Audit 2026-07-20 #12).

The Etsy/Printify/Pinterest/Tumblr clients had NO 429 handling — a live audit hit
Etsy 429s on rapid sequential calls, and under normal growth (more listings x
hourly stats + marketing bursts) unhandled 429s cause failed reads/writes and,
worse, repeated hammering Etsy can read as abuse and throttle/suspend the app (an
existential, revenue-ending event).

request_with_backoff() wraps a single httpx call: on 429/5xx it retries with
exponential backoff + jitter, honoring the server's Retry-After header when
present, up to a capped number of attempts. It returns the final httpx.Response
(success or the last failure) so callers keep their existing raise_for_status()
/ status handling unchanged.
"""
import asyncio
import logging
import random
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

logger = logging.getLogger("ai-factory")

RETRY_STATUSES = (429, 500, 502, 503, 504)
# DEEP AUDIT V2 #11: 5xx retries are only SAFE on idempotent methods. Retrying a
# POST /listings or POST /pins on a 5xx returned AFTER the resource was created
# double-creates it (Etsy/Pinterest have no idempotency key). For mutating methods
# we retry ONLY on 429 (rate-limit — the request provably did not take effect).
_IDEMPOTENT_METHODS = ("GET", "HEAD", "OPTIONS")
_RATE_LIMIT_ONLY = (429,)


def _parse_retry_after(value) -> float | None:
    """Retry-After may be seconds (int) or an HTTP date. Return seconds or None."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        pass
    return None


async def request_with_backoff(
    client,
    method: str,
    url: str,
    *,
    max_retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_statuses=RETRY_STATUSES,
    **kwargs,
):
    """Perform client.request(method, url, **kwargs) with retry/backoff on
    rate-limit (429) and transient 5xx. Honors Retry-After; exponential backoff
    with jitter otherwise; caps at max_retries. Returns the final Response."""
    # Call the verb method (client.get/post/patch/...) rather than client.request:
    # httpx.AsyncClient supports both, but code and tests commonly wrap the client
    # exposing only verb methods, so this stays maximally compatible.
    verb = getattr(client, method.lower())
    # #11: non-idempotent methods retry ONLY on 429 (safe); idempotent methods may
    # also retry transient 5xx.
    if method.upper() not in _IDEMPOTENT_METHODS:
        retry_statuses = tuple(s for s in retry_statuses if s in _RATE_LIMIT_ONLY)
    attempt = 0
    while True:
        response = await verb(url, **kwargs)
        if response.status_code not in retry_statuses or attempt >= max_retries:
            return response

        delay = _parse_retry_after(response.headers.get("Retry-After"))
        if delay is None:
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay += random.uniform(0, delay * 0.25)  # jitter to de-sync bursts
        delay = min(delay, max_delay)
        logger.warning(
            f"http_backoff: {response.status_code} on {method} {url} — retrying in "
            f"{delay:.1f}s (attempt {attempt + 1}/{max_retries})"
        )
        await asyncio.sleep(delay)
        attempt += 1


def _next_delay(response, attempt, base_delay, max_delay):
    delay = _parse_retry_after(response.headers.get("Retry-After"))
    if delay is None:
        delay = min(max_delay, base_delay * (2 ** attempt))
        delay += random.uniform(0, delay * 0.25)
    return min(delay, max_delay)


def request_with_backoff_sync(
    client,
    method: str,
    url: str,
    *,
    max_retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_statuses=RETRY_STATUSES,
    **kwargs,
):
    """Synchronous twin of request_with_backoff for httpx.Client-based clients
    (e.g. PrintifyClient). Same policy: mutating methods retry only on 429."""
    verb = getattr(client, method.lower())
    if method.upper() not in _IDEMPOTENT_METHODS:
        retry_statuses = tuple(s for s in retry_statuses if s in _RATE_LIMIT_ONLY)
    attempt = 0
    while True:
        response = verb(url, **kwargs)
        if response.status_code not in retry_statuses or attempt >= max_retries:
            return response
        delay = _next_delay(response, attempt, base_delay, max_delay)
        logger.warning(
            f"http_backoff(sync): {response.status_code} on {method} {url} — retrying in "
            f"{delay:.1f}s (attempt {attempt + 1}/{max_retries})"
        )
        time.sleep(delay)
        attempt += 1
