# Step 101 — Dense pixel-art room scenes + wandering robots (dashboard)

**Date:** 2026-07-10

Frontend-only (`frontend/index.html`). Upgrades the facility dashboard from
sparse single-shape room decorations to dense, distinct pixel-art scenes, and
replaces the robots' left-right bounce with real path-based wandering.
**Zero image-generation calls, zero external image/sprite files** — every pixel
is composed in JS from 2D colour grids (verified: no ImageProvider / generation
call anywhere; scenes are `<canvas>` data-URIs built from JS grids; JSX
esbuild-validated).

## STEP 1 — Dense scenes (same grid technique, rendered to a scaled canvas)

Each room's scene is a JS 2D colour grid (84×48) composed from **reusable element
"stamps"** — not one-off CSS or hand-placed cells — then rendered **once** to a
tiny `<canvas>` → `data:` URI used as a `image-rendering: pixelated`,
`background-size: 100% 100%` background, so it scales crisply to the responsive
tile. This is the "equivalent scalable approach" to the robot's box-shadow list
(box-shadow doesn't scale to a fluid tile; a 4000-shadow list per room would also
be far heavier). ~2300–2500 filled cells per room (≈60% density), 7–13 colours.

Shared stamp toolkit: `stampFloor` (tiled floor + grid lines + horizon),
`cabinet`, `screen` (scan-lined), `monitor`, `crate`, `shelf`, `telescope`,
`antenna`, `rings` (broadcast), `bars` (chart), `coins`, `pinboard`, `easel`,
`paintcans`, `magnifier`, `counter`, `sign`, `lights`, plus `LN` (Bresenham).
Palette: dark→light metals, dark screens, white/bulb/red/green, and the room's
**accent** (glowing panels) + a lightened accent highlight. Each room also gets a
radial accent **`.scene-glow`** "lighting" wash.

Per-room composition (machinery on the back wall above the horizon, open floor in
front):
- **Research** — star-field sky + a console bank with a screen + a monitor + a
  telescope on a tripod + status lights.
- **Planning** — console+screen + a pinboard of notes + a second cabinet with a
  small monitor + lights.
- **Content** — a writing console with a monitor + a pinboard of notes + a side
  cabinet.
- **Image Studio** — an easel with a canvas + a shelf of paint-tins + a
  paint-cans row + a preview monitor.
- **QA** — a large scan-lined screen console + a magnifying glass + a low cabinet
  + green/amber/red status lights.
- **Storefront** — a hanging sign + two stocked shelves + a counter with a
  register.
- **Marketing** — a broadcast antenna with concentric signal rings + a console
  with a screen + lights.
- **Fulfillment** — a conveyor band (see animation below) + three stacked crates
  + a cabinet.
- **Ledger** — a terminal screen showing a bar chart + a stack of coins + a
  cabinet.

**Fulfillment** additionally gets a CSS-animated `.scene-conveyor` overlay
(scrolling repeating-gradient) aligned to the belt drawn in the scene — the one
bit of motion a static scene image can't convey.

## STEP 2 — Wandering robots

Each room renders one `.robot-actor` per real character (still 1, per the
single-threaded-worker concurrency logic). A per-room **`requestAnimationFrame`**
loop (`startWander`) moves each robot:
- picks a random target inside the **walkable floor band** (`WALK` = x 5–95%, y
  54–90% of the floor — the open area in front of the machinery, robot size
  subtracted so it never leaves the floor);
- glides toward it at a **consistent px/s** (speed ∝ floor width, so it feels the
  same regardless of distance/room size), then pauses a random time and picks a
  new target;
- flips `scaleX(±1)` on a `.robot-facing` wrapper to **face its travel
  direction**;
- keeps playing the existing `.robot--working` / `.robot--idle` walk-cycle
  (whichever the room's real state calls for) **while** translating.

**Activity levels** (`WANDER`): working = brisk speed, short 0.25–0.9 s pauses
(energetic); idle = gentle speed, long 1.6–4.2 s pauses (present but calm); stale
= barely moves (3–7 s pauses) + the existing dimmed/greyed style; error = jittery.
So idle vs working is distinguishable both by walk-cycle style (as before) **and**
by how much/often they roam. Multiple robots would each get an independent
controller (own random targets/timing) — no lockstep.

## STEP 3 — Performance

- The static scene is generated **once** per room via `useMemo([rkey, accent])`
  — never on the 7 s poll.
- Robot positions are written straight to `transform: translate3d(...)` from the
  rAF loop (no React re-render per frame). The wander controller only re-inits
  when the robot **count/state** actually changes (a signature dep), not every
  poll — positions stay continuous across polls (React reuses the actor DOM
  nodes). `ResizeObserver` caches floor size instead of measuring every frame.
- Total: 9 small canvas renders on load + 9 rAF loops each moving 1 actor — light
  on desktop and mobile.

## Verify

- Scene density visually confirmed by rendering each grid to ASCII (star field +
  console bank + telescope + floor tiling + lights clearly present for research;
  all 9 compose without error, ~2.3–2.5k cells each) — a clear step up from the
  single-shape decorations.
- Robots wander to varied random points across the floor (not a two-point
  bounce), facing their direction of travel; working roam briskly/often, idle
  drift slowly/rarely.
- Robot count/state still driven entirely by real `/dashboard/rooms/status` data.
- **No image-generation calls / no external image files** anywhere.
- JSX validated with esbuild (0 errors); deployed to prod, new markers confirmed
  served. (Maj: eyeball at `/ui` — I can't screenshot.)
