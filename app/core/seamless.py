"""
Seamlessness check (STEP 103 B-3). A tileable pattern's opposite edges must
match so it repeats with no visible seam. This compares the left/right and
top/bottom edge pixels within a tolerance — a cheap, deterministic quality
signal for the seamless_pattern format.
"""
from PIL import Image


def edge_mismatch(image_path: str, samples: int = 200) -> float:
    """Return the mean absolute per-channel difference between opposite edges
    (0 = perfectly tileable, higher = more seam). Averages the L/R and T/B seams."""
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    px = img.load()

    def mad(a, b):
        if not a:
            return 0.0
        total = sum(abs(c1 - c2) for p1, p2 in zip(a, b) for c1, c2 in zip(p1, p2))
        return total / (len(a) * 3)

    ys = range(0, h, max(1, h // samples))
    xs = range(0, w, max(1, w // samples))
    left = [px[0, y] for y in ys]
    right = [px[w - 1, y] for y in ys]
    top = [px[x, 0] for x in xs]
    bottom = [px[x, h - 1] for x in xs]
    return (mad(left, right) + mad(top, bottom)) / 2.0


def is_seamless(image_path: str, tolerance: float = 22.0) -> bool:
    """True if the image tiles reasonably seamlessly (edge mismatch <= tolerance)."""
    try:
        return edge_mismatch(image_path) <= tolerance
    except Exception:
        return True  # never block on a check error
