"""
Alert service -- step 86.

POSTs to DISCORD_WEBHOOK_URL. Never raises: a failed alert must not kill
the worker that's trying to report a problem.

Alert on:
  - Worker thread death (permanent, not a retry)
  - Permanent FulfillmentRecord failure after all retries
  - Daily spend cap being hit

Do NOT alert on:
  - Routine retries / JSON repair attempts
  - External API rate limits that succeed on retry
"""
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("ai-factory")

_COLORS = {"error": 16711680, "warning": 16776960, "info": 3447003}


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
        POST an embed to Discord. Returns True on success, False on failure.
        Never raises.
        """
        if not self._url:
            logger.warning(f"AlertService: DISCORD_WEBHOOK_URL not set, dropping alert: {title}")
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
