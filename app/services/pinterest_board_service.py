"""
PinterestBoardService — route each pin to a topically-focused board instead of
one catch-all board. Pinterest's distribution favors tight, on-topic boards, so
a "Greeting Cards" / "Christmas Gifts & Cards" board of related pins outperforms
a single mixed board.

Boards are auto-created on demand and remembered in a durable map on the volume
(data/pinterest_boards.json), reconciled once against the account's live boards
so a lost map never creates duplicates. Everything is best-effort: any failure
returns None so the caller falls back to the configured default board.
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from app.core.paths import get_data_dir

logger = logging.getLogger("ai-factory")

# Curated, bounded board names per product format (nice, on-topic, ~one each).
FORMAT_BOARDS = {
    "greeting_card_design": "Greeting Cards",
    "coloring_page": "Coloring Pages",
    "pdf_planner_or_guide": "Planners & Printables",
    "single_print": "Wall Art Prints",
    "sticker_sheet_design": "Sticker Sheets",
    "pod_apparel_design": "Graphic Tees",
    "pod_mug": "Mugs & Drinkware",
    "pod_poster": "Art Posters",
    "wall_art_set_3": "Gallery Wall Art Sets",
}
DEFAULT_BOARD_NAME = "Shop Favorites"


class PinterestBoardService:
    FILE = "pinterest_boards.json"

    def __init__(self):
        self._dir = get_data_dir()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._reconciled = False

    def _path(self) -> Path:
        return self._dir / self.FILE

    def _load(self) -> dict:
        p = self._path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save(self, m: dict):
        p = self._path()
        tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(m), encoding="utf-8")
        os.replace(tmp, p)

    # ── board naming ─────────────────────────────────────────────────────────
    @staticmethod
    def board_name_for(listing: dict) -> str:
        """Prefer a seasonal-occasion board (bounded set → coherent collections),
        else the product-format board. Both are small, on-topic buckets."""
        title = listing.get("title") or listing.get("product_name") or ""
        desc = listing.get("description") or ""
        try:
            from app.core.seasonality import occasion_for
            occ = occasion_for(title, desc)
            if occ:
                return f"{occ.replace('_', ' ').title()} Gifts & Cards"
        except Exception:
            pass
        fmt = listing.get("product_format") or listing.get("type")
        return FORMAT_BOARDS.get(fmt, DEFAULT_BOARD_NAME)

    # ── resolution ───────────────────────────────────────────────────────────
    async def resolve_for(self, listing: dict) -> Optional[str]:
        """Board id for this listing's theme, creating the board if needed.
        None on any failure (caller falls back to the default board)."""
        return await self.ensure_board(self.board_name_for(listing))

    async def ensure_board(self, name: str) -> Optional[str]:
        m = self._load()
        if name in m:
            return m[name]
        try:
            from app.services.pinterest_oauth import list_boards, create_board
            # Adopt existing boards once so we never duplicate an account board.
            if not self._reconciled:
                for b in await list_boards():
                    bn, bid = b.get("name"), b.get("id")
                    if bn and bid:
                        m.setdefault(bn, str(bid))
                self._reconciled = True
                if name in m:
                    self._save(m)
                    return m[name]
            created = await create_board(
                name, description=f"{name} — original printable & handmade designs.")
            bid = created.get("id")
            if bid:
                m[name] = str(bid)
                self._save(m)
                logger.info(f"PinterestBoardService: created board '{name}' ({bid})")
                return str(bid)
        except Exception as e:
            logger.warning(f"PinterestBoardService: could not resolve board '{name}': {e}")
        return None
