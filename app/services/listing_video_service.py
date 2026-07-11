"""
ListingVideoService (STEP 104 3-4) — a short, deterministic ken-burns video of
the verified design.

Etsy ranks and converts listings with video noticeably better, and a 5-8s slow
pan/zoom over the ALREADY-VERIFIED product image is 100% deterministic
(PIL frames -> ffmpeg via imageio-ffmpeg, which bundles a static ffmpeg binary
so there's no system dependency). Zero image-generation, zero extra spend. The
same MP4 is reusable for Pinterest video pins.

Everything is best-effort: a video is a bonus, never a reason to fail a publish.
"""
import logging
import math
import os

from PIL import Image

logger = logging.getLogger("ai-factory")


class ListingVideoService:
    def __init__(self, fps: int = 24, seconds: float = 6.0, zoom: float = 0.18):
        self.fps = fps
        self.seconds = seconds
        self.zoom = zoom  # fraction to zoom in over the clip (0.18 = 18%)

    def _frame(self, base: Image.Image, t: float, out_w: int, out_h: int) -> Image.Image:
        """One ken-burns frame at normalized time t in [0,1]: ease-in-out zoom
        plus a gentle diagonal drift, cropped and resized to the output size."""
        bw, bh = base.size
        # ease-in-out so the motion starts/stops smoothly
        e = 0.5 - 0.5 * math.cos(math.pi * t)
        scale = 1.0 + self.zoom * e
        crop_w, crop_h = bw / scale, bh / scale
        # drift the crop box across the image (small, diagonal)
        max_dx, max_dy = (bw - crop_w), (bh - crop_h)
        cx = max_dx * (0.25 + 0.5 * e)
        cy = max_dy * (0.25 + 0.5 * e)
        box = (int(cx), int(cy), int(cx + crop_w), int(cy + crop_h))
        return base.crop(box).resize((out_w, out_h), Image.LANCZOS)

    def render(self, source_image_path: str, out_path: str,
               out_size: tuple = (1080, 1080)) -> str:
        """Render a ken-burns MP4 from source_image_path to out_path. Returns the
        output path. Raises on hard failure (caller treats as best-effort)."""
        import imageio  # local import: optional dependency, only loaded on use

        base = Image.open(source_image_path).convert("RGB")
        out_w, out_h = out_size
        n_frames = max(2, int(self.fps * self.seconds))

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        # macro_block_size=1 avoids imageio resizing our exact dimensions; yuv420p
        # + libx264 is the broadly-compatible combination Etsy/Pinterest accept.
        writer = imageio.get_writer(
            out_path, fps=self.fps, codec="libx264", quality=8,
            macro_block_size=1, pixelformat="yuv420p",
        )
        try:
            for i in range(n_frames):
                t = i / (n_frames - 1)
                frame = self._frame(base, t, out_w, out_h)
                writer.append_data(_to_ndarray(frame))
        finally:
            writer.close()
        logger.info(f"ListingVideoService: rendered {n_frames}-frame video -> {out_path}")
        return out_path


def _to_ndarray(img: Image.Image):
    import numpy as np
    return np.asarray(img)
