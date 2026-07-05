from abc import ABC, abstractmethod
from typing import Any, Dict


class MarketingChannel(ABC):
    """
    Abstract contract for a marketing automation channel (Pinterest,
    future channels like Instagram/TikTok). Concrete channels implement
    post() to actually publish a listing to that platform.
    """

    name: str = "base"

    @abstractmethod
    def post(self, listing: Dict[str, Any]) -> Dict[str, Any]:
        """
        Publish content derived from a listing to this channel.
        Returns a dict describing the result, e.g.
        {"success": bool, "external_id": str | None, "url": str | None, "error": str | None}
        """
        raise NotImplementedError