# Step 100 — Themed room scenes + pure-CSS pixel-art robots on the dashboard

**Date:** 2026-07-09

Frontend-only feature (`frontend/index.html`). Decorates each facility room
with a themed CSS backdrop and gives each room a chunky pixel-art robot that
animates based on **real** system state. No backend endpoints were added — it
reuses the existing `GET /dashboard/rooms/status` payload.

---

## Zero image-generation calls — confirmed

**No call to `ImageProviderManager` or any image API was made anywhere in this
feature — not once.** Every visual is pure CSS/HTML: gradients, CSS shapes,
and the box-shadow pixel-grid technique. Verified: `grep` for
`ImageProvider|generate_image|dalle|seedream|stability|/images/generate` in
`frontend/index.html` → **0 hits**. No external image/sprite files are fetched
or embedded either — the robot grid is built directly in JS/CSS.

## The pixel-art technique

The robot is defined as a readable **2D character grid** (`ROBOT_GRID`, 11×16),
one character per pixel:

```
.....K.....   . = transparent   K = black outline
.KKKKKKKKK.   W = white body     V = dark visor
.KVCCVCCVK.   C = cyan eyes      B = blue accent (per-room tint)
KBBWWWWWBBK   ...
```

A small JS function (`buildShadows`) walks the grid at load time and emits one
`box-shadow` entry per non-transparent cell (`Xpx Ypx 0 0 <color>`). A single
3px "pixel" element carries the whole ~135-entry shadow list as the body; the 4
eye pixels are emitted to a **separate overlay element** so they can blink
independently. The shadow strings are computed **once** and shared by every
robot — the blue accent pixels resolve to `var(--robot-accent)`, so each room
tints its robot to that room's existing accent colour without needing a
separate grid. No hand-written coordinates, no build step, no image files.

Reference look recreated: white/light-grey blocky body, blue shoulder/chest
accent panels, dark rounded visor with two bright cyan eyes, black outline on
every shape, antenna on top.

Performance (Step 4): 139 shadows/robot × 9 rooms ≈ 1.25k shadows total, low
enough for mobile. All motion is **CSS animation only** (no JS per-frame loop):
idle/working states are CSS classes; the working blink animates a 4-shadow
overlay's opacity; the walk/breathe animate the container `transform` (GPU
composited).

### Two robot states (CSS-driven)

- **`.robot--idle`** — slow ~2.6s breathing bob (gentle translate + scaleY).
  Calm, clearly "waiting," not static-dead.
- **`.robot--working`** — fast 0.42s side-to-side waddle (translateX + rotate
  about the feet) **plus blinking cyan eyes** (1.5s snap blink). Distinct at a
  glance from idle.
- Bonus states reusing the same sprite: `.robot--stale` (worker offline —
  dimmed, greyed, very slow drift) and `.robot--error` (fast shudder).

## Themed room scenes (Step 2)

Each room now renders a `.scene .scene--<key>` backdrop — a handful of
gradients / pseudo-element shapes per room, low opacity, tinted with the room's
`--rc`, sitting behind the text/robot (`z-index`):

| Room | Motif |
|------|-------|
| Research | scattered radial-gradient star map + tilted telescope tube |
| Planning | clipboard rectangle + checklist lines |
| Content | document with text lines + a clip-path pencil |
| Image Studio | canvas rectangle + clip-path easel tripod |
| QA | magnifying glass (circle lens + rotated handle) |
| Storefront | striped repeating-gradient awning + counter |
| Marketing | megaphone (clip-path) + radial broadcast rings |
| Fulfillment | stacked boxes (box-shadow copies) + dashed conveyor belt |
| Ledger | stacked coins (box-shadow ellipses) + ledger book |

## Robot count + state wired to REAL data only (Step 3)

**Concurrency-model finding (checked the actual code, not assumed):** every
worker in this codebase is **single-threaded and sequential**:

- `TaskWorker` — docstring is explicit: *"One task is processed at a time
  (single worker thread)."* One dequeue → one `process()` at a time.
- `EtsyReceiptWorker` — one daemon thread; receipts and their transactions are
  processed in a plain sequential `for` loop. At most one is mid-processing.
- `AutonomyWorker` / `MarketingRefreshWorker` — same single-thread pattern.

There is **no genuine concurrency anywhere**, so **every room renders exactly
one robot** (never multiple simultaneously-"working" robots, which would
misrepresent real system state). Surplus queued work is shown as a small text
**`⏳ N queued` badge**, not a fake extra robot:

- **Planning** → `counts.queue` (real `TaskQueue` depth waiting for the worker).
- **QA** → `max(0, QA - 1)` (one task validated at a time; the rest are queued).
- **Marketing** → `counts.pending` (posts awaiting send).

Robot state maps straight off the server's already-computed real per-room
`status` (from heartbeat age / real in-progress task counts / recent-activity
counts in `/dashboard/rooms/status`):

- `active → working`, `idle → idle`, `stale → stale` (worker offline),
  `error → error`.
- Worker-backed rooms (Research, Fulfillment) use heartbeat freshness;
  task-lifecycle rooms (Planning, Content, QA) use real in-progress counts;
  activity rooms (Image Studio, Storefront, Marketing, Ledger) use the existing
  recent-activity fields. No new fields invented.

## Live verification

Ran the app locally and drove a **real** task through its lifecycle via the
actual API:

- `content` room baseline: `status=idle, RUNNING=0` → robot **idle**.
- `PATCH /tasks/{id}/status → RUNNING`: `status=active, RUNNING=1` → robot
  **WORKING**.
- Task resolved (`RUNNING=0`): `status=idle` → robot **idle** again.

Confirmed the full **idle → working → idle** transition tracks real task state.
Live snapshot also showed a genuine split: Planning/Marketing/Fulfillment/Ledger
`active` (from real tasks/posts/events/heartbeats) vs Research/Content/QA/
Storefront/Image-Studio `idle` (genuinely zero activity). `/ui/index.html`
serves the new markup (HTTP 200, all robot/scene markers present).
