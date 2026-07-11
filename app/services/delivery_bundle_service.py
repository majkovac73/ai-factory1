"""
DeliveryBundleService (STEP 103 A-5) — turn ONE content-verified master design
into the MULTI-FILE bundle buyers of each format actually expect, using pure
Pillow (zero image-generation cost). Etsy allows up to 5 digital files/listing;
the pipeline used to upload 1.

- single_print: multiple standard print ratios (2:3, 3:4, 4:5, ISO A) by smart
  center-crop, so the buyer can print to any common frame — "5 sizes included"
  is what top wall-art listings advertise.
- phone_wallpaper: a couple of common device resolutions.
- coloring_page: the original PNG PLUS a ready-to-print letter-size PDF with
  margins (buyers print these — a PDF reviews better than a raw PNG).
- other formats: just the original (no bundling).
"""
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger("ai-factory")

# Portrait print ratios (w, h) for wall art, cropped from a portrait master.
_PRINT_RATIOS = [
    ("2x3", 2, 3),      # 4x6, 8x12, 12x18, 24x36
    ("3x4", 3, 4),      # 6x8, 9x12, 12x16, 18x24
    ("4x5", 4, 5),      # 8x10, 16x20
    ("ISO_A", 1000, 1414),  # A5/A4/A3 (√2)
]
_PRINT_LONG_EDGE = 3600  # ~300 dpi at 12in — real print quality

# Common phone wallpaper pixel sizes (portrait 9:19.5-ish and 9:16).
_PHONE_SIZES = [(1170, 2532), (1080, 2340)]

_LETTER_PX = (2550, 3300)  # 8.5x11 at 300 dpi
_LETTER_MARGIN = 150


class DeliveryBundleService:
    def build(self, master_path, product_format: str) -> list:
        """Return a list of delivery file Paths (master first). Never raises —
        on any error returns just the master so the pipeline still delivers."""
        try:
            master = Path(master_path)
            if not master.exists():
                return [Path(master_path)]
            if product_format == "single_print":
                return self._print_bundle(master)
            if product_format == "phone_wallpaper":
                return self._phone_bundle(master)
            if product_format == "coloring_page":
                return self._coloring_bundle(master)
            if product_format == "greeting_card_design":
                return self._card_bundle(master)
            return [master]
        except Exception as e:
            logger.warning(f"DeliveryBundleService: bundle failed for {product_format}: {e}; delivering master only")
            return [Path(master_path)]

    # ── per-format bundles ────────────────────────────────────────────────────

    def _print_bundle(self, master: Path) -> list:
        img = Image.open(master).convert("RGB")
        out = [master]  # keep the original master as the highest-res file
        for name, rw, rh in _PRINT_RATIOS:
            if rw >= rh:
                w, h = _PRINT_LONG_EDGE, int(_PRINT_LONG_EDGE * rh / rw)
            else:
                w, h = int(_PRINT_LONG_EDGE * rw / rh), _PRINT_LONG_EDGE
            variant = ImageOps.fit(img, (w, h), Image.LANCZOS)
            p = master.parent / f"{master.stem}_{name}.png"
            variant.save(p, format="PNG")
            out.append(p)
        return out[:5]

    def _phone_bundle(self, master: Path) -> list:
        img = Image.open(master).convert("RGB")
        out = [master]
        for (w, h) in _PHONE_SIZES:
            variant = ImageOps.fit(img, (w, h), Image.LANCZOS)
            p = master.parent / f"{master.stem}_{w}x{h}.png"
            variant.save(p, format="PNG")
            out.append(p)
        return out[:5]

    def _coloring_bundle(self, master: Path) -> list:
        img = Image.open(master).convert("RGB")
        page = Image.new("RGB", _LETTER_PX, (255, 255, 255))
        avail = (_LETTER_PX[0] - 2 * _LETTER_MARGIN, _LETTER_PX[1] - 2 * _LETTER_MARGIN)
        art = ImageOps.contain(img, avail, Image.LANCZOS)
        page.paste(art, ((_LETTER_PX[0] - art.width) // 2, (_LETTER_PX[1] - art.height) // 2))
        pdf_path = master.parent / f"{master.stem}_letter.pdf"
        page.save(pdf_path, format="PDF", resolution=300.0)
        return [master, pdf_path]

    def _card_bundle(self, master: Path) -> list:
        """B-2: a print-ready HALF-FOLD card PDF (landscape letter, fold down the
        middle; art on the right = the front when folded, small back note on the
        left) plus the original art PNG. A flat PNG doesn't fold into a card."""
        img = Image.open(master).convert("RGB")
        W, H = 3300, 2550  # landscape letter @ 300 dpi
        half = W // 2
        margin = 160
        sheet = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        draw.line([(half, 0), (half, H)], fill=(210, 210, 210), width=2)  # fold guide
        art = ImageOps.contain(img, (half - 2 * margin, H - 2 * margin), Image.LANCZOS)
        sheet.paste(art, (half + (half - art.width) // 2, (H - art.height) // 2))
        try:
            f = ImageFont.load_default(size=40)
            draw.text((margin, H - 140), "Printable greeting card — print & fold in half.", fill=(150, 150, 150), font=f)
        except Exception:
            pass
        pdf_path = master.parent / f"{master.stem}_card.pdf"
        sheet.save(pdf_path, format="PDF", resolution=300.0)
        return [master, pdf_path]

    @staticmethod
    def size_summary(product_format: str, n_files: int) -> str:
        """A short 'Includes N sizes/files' line for the description (A-4/A-5)."""
        if product_format == "single_print" and n_files > 1:
            return f"Includes {n_files} print ratios (2:3, 3:4, 4:5, A-series + original) so it fits any standard frame."
        if product_format == "phone_wallpaper" and n_files > 1:
            return f"Includes {n_files} device sizes."
        if product_format == "coloring_page" and n_files > 1:
            return "Includes a ready-to-print letter-size PDF and the original PNG."
        if product_format == "greeting_card_design" and n_files > 1:
            return "Includes a print-ready fold-over card PDF and the original art."
        return ""
