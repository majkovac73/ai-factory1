# Step 100j — Coloring-page delivery files get a pure white background

**Date:** 2026-07-09

## Problem

Maj spotted a faint grey **checkerboard** behind the design in the listing
mockups. Inspecting the real delivery file confirmed it's **baked into the RGB
pixels** of the actual delivered coloring page (mode RGB, fully opaque; background
greys ~244–252, not pure white) — a Seedream "transparent background" artifact. So
the buyer's downloaded coloring page wasn't clean white. (Only matters on the
product; anywhere else is cosmetic.)

## Fix

`PipelineOrchestrator._flatten_white_background(path)` whitens the near-white/
checkerboard background to pure white **in place** in the delivered file, while
preserving the black line art exactly. It only whitens pixels that are near-white
in **all** channels (per-pixel `min(R,G,B) >= 234`, via `ImageChops.darker`), so
any pixel with a dark or coloured channel (line art, real content) is untouched —
no numpy, C-level PIL ops (fast on 2048².

`_stage_pod_design` calls it for **`coloring_page` only**, right after generation
and before the file is validated, registered, or used to build the listing
mockups — so the buyer's download, the catalog delivery asset, and the mockups are
all clean. Verified on the real owl file: background → pure white, line-art dark
pixels unchanged (714384 → 714384), visually confirmed.

## Tests

`scripts/test_step100j_white_background.py` (3/3): checkerboard → pure white with
line art preserved exactly; coloured/dark content is never whitened (only
near-white-in-all-channels); the pipeline applies it for coloring_page and **not**
for other formats. Full regression green (16 suites).
