"""
PlannerPageRenderer (STEP 103 A-6) — render planner/guide INTERIOR pages
deterministically with Pillow instead of image-generating them.

Planner pages are structured layouts (grids, ruled lines, checkboxes, tables) —
exactly what an image model renders WORST (misspelled headings, wonky grids that
fail QA -> retries -> cost) and a code renderer does PERFECTLY. This produces
crisp, always-legible pages at ~$0 each, so a planner can be 20-30 pages instead
of 6, competitive with the market. The decorative COVER is still image-generated
(that's what Seedream is good at).

No binary fonts shipped: Pillow 10.1+ `ImageFont.load_default(size=...)` gives a
scalable sans-serif that's clean for headings/labels/grids.
"""
import logging

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("ai-factory")

# Letter-ish portrait at ~200 dpi (P1-5 downscales the assembled PDF anyway).
PAGE_W, PAGE_H = 1700, 2200
MARGIN = 120
INK = (30, 30, 30)
LINE = (170, 170, 170)
LIGHT = (210, 210, 210)

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_LAYOUTS = {"weekly_grid", "checklist", "lined", "tracker_table", "monthly_calendar", "dotted"}


def _font(size):
    return ImageFont.load_default(size=size)


class PlannerPageRenderer:
    def render(self, spec: dict) -> Image.Image:
        """Render a page spec {heading, layout, labels[]} to a PIL image."""
        layout = (spec.get("layout") or "lined")
        if layout not in _LAYOUTS:
            layout = "lined"
        img = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        y = self._heading(draw, spec.get("heading") or "")
        getattr(self, f"_{layout}")(draw, y, spec.get("labels") or [])
        return img

    # ── heading ───────────────────────────────────────────────────────────────

    def _heading(self, draw, heading: str) -> int:
        f = _font(64)
        draw.text((MARGIN, MARGIN), heading[:60], fill=INK, font=f)
        y = MARGIN + 100
        draw.line([(MARGIN, y), (PAGE_W - MARGIN, y)], fill=INK, width=3)
        return y + 40

    # ── layouts ─────────────────────────────────────────────────────────────────

    def _lined(self, draw, y, labels):
        gap = 70
        while y < PAGE_H - MARGIN:
            draw.line([(MARGIN, y), (PAGE_W - MARGIN, y)], fill=LINE, width=2)
            y += gap

    def _dotted(self, draw, y, labels):
        gap = 60
        x0 = MARGIN
        while y < PAGE_H - MARGIN:
            x = x0
            while x < PAGE_W - MARGIN:
                draw.ellipse([(x - 3, y - 3), (x + 3, y + 3)], fill=LIGHT)
                x += gap
            y += gap

    def _checklist(self, draw, y, labels):
        f = _font(40)
        box = 44
        gap = 78
        rows = labels or [""] * 18
        for label in rows:
            if y > PAGE_H - MARGIN:
                break
            draw.rectangle([(MARGIN, y), (MARGIN + box, y + box)], outline=INK, width=3)
            if label:
                draw.text((MARGIN + box + 25, y + 4), str(label)[:60], fill=INK, font=f)
            else:
                draw.line([(MARGIN + box + 25, y + box), (PAGE_W - MARGIN, y + box)], fill=LINE, width=2)
            y += gap

    def _weekly_grid(self, draw, y, labels):
        f = _font(38)
        rows = _WEEKDAYS
        avail = PAGE_H - MARGIN - y
        rh = min(200, avail // len(rows))
        for day in rows:
            draw.rectangle([(MARGIN, y), (PAGE_W - MARGIN, y + rh)], outline=LINE, width=2)
            draw.text((MARGIN + 20, y + 15), day, fill=INK, font=f)
            # ruled writing lines within the day block
            ly = y + 75
            while ly < y + rh - 20:
                draw.line([(MARGIN + 20, ly), (PAGE_W - MARGIN - 20, ly)], fill=LIGHT, width=1)
                ly += 45
            y += rh

    def _tracker_table(self, draw, y, labels):
        f = _font(30)
        cols = 8  # label + 7 days/marks
        col_w = (PAGE_W - 2 * MARGIN) / cols
        rows = labels or [f"Habit {i+1}" for i in range(15)]
        rh = 80
        # header
        draw.text((MARGIN + 10, y + 20), "Item", fill=INK, font=f)
        for c in range(1, cols):
            draw.text((MARGIN + c * col_w + 15, y + 20), _WEEKDAYS[c - 1][:3], fill=INK, font=_font(26))
        y += rh
        for label in rows:
            if y > PAGE_H - MARGIN:
                break
            draw.line([(MARGIN, y + rh), (PAGE_W - MARGIN, y + rh)], fill=LINE, width=1)
            for c in range(cols + 1):
                x = MARGIN + c * col_w
                draw.line([(x, y), (x, y + rh)], fill=LINE, width=1)
            draw.text((MARGIN + 10, y + 22), str(label)[:22], fill=INK, font=f)
            y += rh

    def _monthly_calendar(self, draw, y, labels):
        f = _font(26)
        cols = 7
        rows = 5
        col_w = (PAGE_W - 2 * MARGIN) / cols
        for c in range(cols):
            draw.text((MARGIN + c * col_w + 10, y, ), _WEEKDAYS[c][:3], fill=INK, font=f)
        y += 50
        avail = PAGE_H - MARGIN - y
        rh = avail / rows
        for r in range(rows + 1):
            yy = y + r * rh
            draw.line([(MARGIN, yy), (PAGE_W - MARGIN, yy)], fill=LINE, width=1)
        for c in range(cols + 1):
            xx = MARGIN + c * col_w
            draw.line([(xx, y), (xx, y + rows * rh)], fill=LINE, width=1)

    # ── heuristic spec derivation ───────────────────────────────────────────────

    @staticmethod
    def derive_spec(brief: str) -> dict:
        """Infer a page spec {heading, layout, labels} from a text page brief —
        no LLM call, fully deterministic."""
        text = (brief or "").strip()
        heading = text.split(".")[0].split("\n")[0][:60] or "Planner Page"
        low = text.lower()
        if any(k in low for k in ("month", "calendar")):
            layout = "monthly_calendar"
        elif any(k in low for k in ("track", "tracker", "habit", "log")):
            layout = "tracker_table"
        elif any(k in low for k in ("week", "weekly", "daily", "schedule")):
            layout = "weekly_grid"
        elif any(k in low for k in ("checklist", "to do", "to-do", "task", "shopping", "grocery", "list")):
            layout = "checklist"
        elif any(k in low for k in ("dot", "bullet")):
            layout = "dotted"
        else:
            layout = "lined"
        return {"heading": heading, "layout": layout, "labels": []}
