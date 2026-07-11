"""
TextOverlayService (STEP 103 B-4) — set typography deterministically with Pillow.

For text-led concepts (quote prints, affirmation cards) the image model
misspells; content-QA catches it, retries burn $0.04 + vision calls, and stubborn
cases block the task after money was spent. Instead we generate a TEXT-FREE
decorative background with the image model and render the exact words here — so
spelling is guaranteed and QA passes deterministically.

Uses Pillow's sized default font (no binary fonts shipped), word-wraps and
auto-sizes the text to fit, and draws a light outline for contrast on any
background.
"""
import logging

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("ai-factory")


class TextOverlayService:
    def overlay(self, image_path: str, text: str) -> bool:
        """Render `text` centered onto the image at image_path (in place).
        Returns True on success, False on failure (leaves the file untouched)."""
        if not text or not text.strip():
            return False
        try:
            img = Image.open(image_path).convert("RGB")
            W, H = img.size
            draw = ImageDraw.Draw(img)
            words = text.strip().split()

            # Auto-size: shrink until the wrapped text fits the central 80% box.
            box_w, box_h = int(W * 0.8), int(H * 0.6)
            size = max(24, W // 12)
            while size >= 24:
                font = ImageFont.load_default(size=size)
                lines = self._wrap(words, font, box_w, draw)
                line_h = int(size * 1.35)
                total_h = line_h * len(lines)
                widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
                if total_h <= box_h and widest <= box_w:
                    break
                size -= max(4, size // 12)

            font = ImageFont.load_default(size=size)
            lines = self._wrap(words, font, box_w, draw)
            line_h = int(size * 1.35)
            y = (H - line_h * len(lines)) // 2
            for ln in lines:
                w = draw.textlength(ln, font=font)
                x = (W - w) // 2
                # light outline for legibility on any background, dark fill
                draw.text((x, y), ln, font=font, fill=(25, 25, 25),
                          stroke_width=max(2, size // 20), stroke_fill=(255, 255, 255))
                y += line_h

            img.save(image_path)
            logger.info(f"TextOverlayService: rendered {len(lines)} line(s) at size {size} onto {image_path}")
            return True
        except Exception as e:
            logger.warning(f"TextOverlayService: overlay failed for {image_path}: {e}")
            return False

    @staticmethod
    def _wrap(words, font, max_w, draw):
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines
