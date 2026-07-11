"""
WallArtSetService (STEP 104 7-1) — deterministic helpers for the wall_art_set_3
format: a curated set of 3 coordinated prints sharing one palette/theme.

The actual image generation lives in the pipeline (PODPipelineService, 3x), but
everything that CAN be deterministic lives here and is unit-tested offline:
  - piece_briefs():        build 3 coordinated generation briefs (shared palette
                           + theme, distinct subjects).
  - palette_consistent():  verify the 3 rendered pieces actually share a palette
                           (a set whose pieces clash is a bad product).
  - compose_triptych():    build the "hangs together" gallery-wall listing photo.
No image-generation, no paid calls.
"""
import logging

from PIL import Image

logger = logging.getLogger("ai-factory")

SET_SIZE = 3


class WallArtSetService:
    # ── coordinated generation briefs ────────────────────────────────────────
    @staticmethod
    def piece_briefs(product_name: str, theme_brief: str) -> list:
        """Return SET_SIZE briefs for the coordinated pieces. Each shares the
        palette/style/mood but depicts a DISTINCT subject so a buyer gets three
        different-but-matching prints (not the same image three times)."""
        roles = [
            "This is the PRIMARY focal piece of the trio.",
            "This is the SECOND, complementary piece — a different subject in the same series.",
            "This is the THIRD coordinating piece that completes the gallery-wall trio — again a distinct subject.",
        ]
        shared = (
            f"Part of a coordinated SET of 3 matching wall-art prints titled '{product_name}'. "
            "ALL three prints MUST share the EXACT same color palette, art style, line weight, "
            "and mood so they read as one gallery-wall set. Keep the composition simple and "
            "centered with even margins. "
        )
        return [f"{shared}{role} {theme_brief}".strip() for role in roles]

    # ── palette-consistency check ────────────────────────────────────────────
    @staticmethod
    def dominant_palette(path: str, k: int = 5) -> list:
        """Return up to k dominant (r,g,b) colors of an image, most-common first."""
        img = Image.open(path).convert("RGB")
        # quantize to k representative colors, then read their populations
        q = img.convert("P", palette=Image.ADAPTIVE, colors=k)
        pal = q.getpalette() or []
        counts = q.getcolors() or []  # list of (count, index)
        counts.sort(reverse=True)
        out = []
        for _, idx in counts[:k]:
            out.append((pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2]))
        return out

    @staticmethod
    def _palette_distance(pa: list, pb: list) -> float:
        """Normalized 0..1 distance between two palettes (mean nearest-color
        Euclidean distance, symmetric)."""
        if not pa or not pb:
            return 1.0

        def nearest(c, pal):
            return min(sum((c[i] - o[i]) ** 2 for i in range(3)) ** 0.5 for o in pal)

        d = (sum(nearest(c, pb) for c in pa) / len(pa)
             + sum(nearest(c, pa) for c in pb) / len(pb)) / 2
        # max possible Euclidean distance in RGB is sqrt(3*255^2) ~= 441.7
        return min(1.0, d / 441.673)

    @classmethod
    def palette_consistent(cls, paths: list, tol: float = 0.42) -> dict:
        """Do all pieces share a palette within `tol`? Returns
        {consistent: bool, max_distance: float, pairs: [...]}."""
        pals = [cls.dominant_palette(p) for p in paths]
        max_d, pairs = 0.0, []
        for i in range(len(pals)):
            for j in range(i + 1, len(pals)):
                d = cls._palette_distance(pals[i], pals[j])
                pairs.append({"a": i, "b": j, "distance": round(d, 4)})
                max_d = max(max_d, d)
        return {"consistent": max_d <= tol, "max_distance": round(max_d, 4), "pairs": pairs}

    # ── gallery-wall listing photo ───────────────────────────────────────────
    @staticmethod
    def compose_triptych(paths: list, out_path: str, cell: int = 900,
                         gap: int = 48, margin: int = 72,
                         bg=(248, 246, 242)) -> str:
        """Compose the 3 pieces side-by-side (framed on a wall-like background)
        into one landscape listing photo that shows they hang together."""
        imgs = [Image.open(p).convert("RGB") for p in paths[:SET_SIZE]]
        # square-crop + resize each piece to a uniform cell
        cells = []
        for im in imgs:
            w, h = im.size
            s = min(w, h)
            im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
            cells.append(im.resize((cell, cell), Image.LANCZOS))
        n = len(cells)
        total_w = margin * 2 + cell * n + gap * (n - 1)
        total_h = margin * 2 + cell
        canvas = Image.new("RGB", (total_w, total_h), bg)
        x = margin
        for im in cells:
            canvas.paste(im, (x, margin))
            x += cell + gap
        canvas.save(out_path, format="PNG")
        logger.info(f"WallArtSetService: composed triptych -> {out_path}")
        return out_path
