"""
Alert service — step 86.

POSTs to DISCORD_WEBHOOK_URL. Never raises: a failed alert must not kill
the worker that's trying to report a problem.

Alert on:
  - Worker thread death (permanent, not a retry)
  - Permanent FulfillmentRecord failure after all retries
  - Daily spend cap being hit

Do NOT alert on:
  - Routine retries / JSON repair attempts
  - External API rate limits that succeed on retry
  - Concurrent-duplicate IntegrityErrors (expected race-condition behavior)

Debouncing: identical alert titles are suppressed for DEBOUNCE_SECONDS after
the first send. This prevents alert storms (e.g. 15 concurrent fulfillment
failures all hitting Discord in the same second and getting rate-limited).
One alert per event type per minute is enough to be actionable.
"""
import logging
import time
from typing import Dict, Optional

import httpx

from config import settings

logger = logging.getLogger("ai-factory")

_COLORS = {"error": 16711680, "warning": 16776960, "info": 3447003}

DEBOUNCE_SECONDS = 60

# Module-level so debouncing is shared across all AlertService instances
# (they are typically created fresh per call, not reused).
_last_sent: Dict[str, float] = {}


class AlertService:
    def __init__(self, webhook_url: Optional[str] = None):
        self._url = webhook_url or getattr(settings, "DISCORD_WEBHOOK_URL", None)

    async def send_alert(
        self,
        title: str,
        message: str,
        level: str = "error",
    ) -> bool:
        """
        POST an embed to Discord. Returns True on success, False on failure or debounce.
        Never raises.
        """
        if not self._url:
            logger.warning(f"AlertService: DISCORD_WEBHOOK_URL not set, dropping alert: {title}")
            return False

        # Debounce: skip if same title was sent within DEBOUNCE_SECONDS
        now = time.monotonic()
        last = _last_sent.get(title, 0.0)
        if now - last < DEBOUNCE_SECONDS:
            logger.debug(
                f"AlertService: debounced '{title}' "
                f"(sent {now - last:.0f}s ago, cooldown {DEBOUNCE_SECONDS}s)"
            )
            return False

        payload = {
            "username": "AI Factory",
            "embeds": [
                {
                    "title": title,
                    "description": message[:2000],
                    "color": _COLORS.get(level, _COLORS["error"]),
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self._url, json=payload)
            if resp.status_code not in (200, 204):
                logger.warning(f"AlertService: Discord returned {resp.status_code}")
                return False
            _last_sent[title] = now
            return True
        except Exception as e:
            logger.warning(f"AlertService: failed to send alert '{title}': {e}")
            return False

    def send_alert_sync(self, title: str, message: str, level: str = "error") -> bool:
        """Synchronous wrapper for use inside background threads."""
        import asyncio
        try:
            return asyncio.run(self.send_alert(title, message, level))
        except Exception as e:
            logger.warning(f"AlertService: send_alert_sync failed: {e}")
            return False
