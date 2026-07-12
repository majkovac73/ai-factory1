"""
Coloring-page whiteness check (STEP 105 1-5).

A coloring page must be UNCOLORED — black line art on white. The 1-8 prompt rule
relies on the image model obeying and a vision model noticing; a half-colored
page can pass both. Whiteness is trivially checkable in code: a real line-art
page is ~all white with a few percent of black outline, and essentially NO pixels
that are colored or grey-shaded. This computes the fraction of "colored" pixels
(neither near-white nor near-black) so the pipeline can reject a pre-colored page
deterministically and regenerate.
"""
from PIL import Image

# A pixel is "near white" or "near black" within these per-channel bounds.
_WHITE_MIN = 235          # all channels >= this -> white paper
_BLACK_MAX = 60           # all channels <= this -> black line
# A near-grey pixel (low saturation) at mid luminance is shading, which counts
# as coloring for our purposes; only near-white/near-black are "not colored".


def color_fraction(path: str, sample_max: int = 400) -> float:
    """Return the fraction (0..1) of pixels that are NEITHER near-white NOR
    near-black — i.e. actual color or grey shading. Downsamples large images to
    at most sample_max on the long edge for speed (deterministic enough)."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if max(w, h) > sample_max:
        scale = sample_max / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
    px = img.load()
    W, H = img.size
    total = W * H
    if total == 0:
        return 0.0
    colored = 0
    for y in range(H):
        for x in range(W):
            r, g, b = px[x, y]
            is_white = r >= _WHITE_MIN and g >= _WHITE_MIN and b >= _WHITE_MIN
            is_black = r <= _BLACK_MAX and g <= _BLACK_MAX and b <= _BLACK_MAX
            if not is_white and not is_black:
                colored += 1
    return colored / float(total)


def is_uncolored(path: str, max_fraction: float = 0.03) -> bool:
    """True if the page reads as clean line art (colored-pixel fraction below
    max_fraction, default 3%)."""
    try:
        return color_fraction(path) <= max_fraction
    except Exception:
        # never let a check I/O error block an otherwise-valid page
        return True
