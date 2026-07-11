"""
Seasonality (STEP 103 A-7, hardened in STEP 104 Tier 1).

Etsy buyers shop occasions weeks ahead and a brand-new listing needs ~1-3 weeks
for Etsy search to settle its ranking, so a listing must be built EARLY enough to
rank before the wave — and NOT built once the wave has passed. This module:

  1-1: computes movable-holiday dates (Easter computus, Nth-weekday rules)
       instead of hardcoding one year's dates (which drift wrong every year).
  1-2: uses PER-EVENT lead windows (Christmas starts ~mid-Sept; min never < 4
       weeks — below that a new listing can't rank in time).
  1-3: exposes occasion_mismatch() — a hard code gate that rejects a concept
       referencing an occasion whose window does NOT include today — plus a
       negative "do NOT build for these" prompt block.
"""
from datetime import date, timedelta


# ── movable-date helpers (1-1) ──────────────────────────────────────────────

def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous computus). Easter 2026=Apr 5, 2027=Mar 28."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The Nth `weekday` (Mon=0..Sun=6) of a month. e.g. 2nd Sunday of May."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)


# ── event table (1-1 dates + 1-2 per-event windows + 1-3 match keywords) ────
# Each event: key, name, date_fn(year)->date, seed keyword, lead window (weeks),
# and the keywords used to detect that a concept is FOR this occasion.
_EVENTS = [
    {"key": "new_year", "name": "New Year / planning season", "date": lambda y: date(y, 1, 1),
     "keyword": "new year planner", "min_w": 2, "max_w": 8,
     "match": ["new year", "resolution", "goal planner", "2026 planner", "2027 planner", "2028 planner"]},
    {"key": "valentines", "name": "Valentine's Day", "date": lambda y: date(y, 2, 14),
     "keyword": "valentines printable", "min_w": 4, "max_w": 9,
     "match": ["valentine", "valentines", "galentine", "cupid"]},
    {"key": "st_patricks", "name": "St. Patrick's Day", "date": lambda y: date(y, 3, 17),
     "keyword": "st patricks day printable", "min_w": 4, "max_w": 9,
     "match": ["st patrick", "st. patrick", "saint patrick", "shamrock", "leprechaun"]},
    {"key": "easter", "name": "Easter / spring", "date": lambda y: easter(y),
     "keyword": "easter printable", "min_w": 4, "max_w": 9,
     "match": ["easter", "easter bunny", "easter egg"]},
    {"key": "mothers_day", "name": "Mother's Day", "date": lambda y: nth_weekday(y, 5, 6, 2),
     "keyword": "mothers day printable", "min_w": 4, "max_w": 9,
     "match": ["mother's day", "mothers day", "mom gift", "gift for mom"]},
    {"key": "graduation", "name": "Graduation season", "date": lambda y: date(y, 6, 1),
     "keyword": "graduation printable", "min_w": 4, "max_w": 9,
     "match": ["graduation", "graduate", "class of", "grad gift"]},
    {"key": "fathers_day", "name": "Father's Day", "date": lambda y: nth_weekday(y, 6, 6, 3),
     "keyword": "fathers day printable", "min_w": 4, "max_w": 9,
     "match": ["father's day", "fathers day", "dad gift", "gift for dad"]},
    {"key": "back_to_school", "name": "Back to school", "date": lambda y: date(y, 9, 1),
     "keyword": "back to school printable", "min_w": 4, "max_w": 10,
     "match": ["back to school", "classroom", "teacher appreciation", "school planner"]},
    {"key": "halloween", "name": "Halloween", "date": lambda y: date(y, 10, 31),
     "keyword": "halloween printable", "min_w": 4, "max_w": 9,
     "match": ["halloween", "spooky", "pumpkin", "trick or treat"]},
    {"key": "thanksgiving", "name": "Thanksgiving", "date": lambda y: nth_weekday(y, 11, 3, 4),
     "keyword": "thanksgiving printable", "min_w": 4, "max_w": 9,
     "match": ["thanksgiving", "turkey day", "friendsgiving", "gratitude"]},
    {"key": "christmas", "name": "Christmas / holidays", "date": lambda y: date(y, 12, 25),
     "keyword": "christmas printable", "min_w": 6, "max_w": 14,
     "match": ["christmas", "xmas", "santa", "holiday gift", "advent"]},
]


def _next_occurrence(ev: dict, today: date) -> date:
    """The next date this event occurs on or after `today`."""
    d = ev["date"](today.year)
    if d < today:
        d = ev["date"](today.year + 1)
    return d


def upcoming_occasions(today: date = None, min_weeks: int = None, max_weeks: int = None) -> list:
    """Occasions whose PER-EVENT lead window includes today, soonest first.
    min_weeks/max_weeks override the per-event windows when given (back-compat)."""
    today = today or date.today()
    out = []
    for ev in _EVENTS:
        d = _next_occurrence(ev, today)
        days = (d - today).days
        lo = (min_weeks if min_weeks is not None else ev["min_w"]) * 7
        hi = (max_weeks if max_weeks is not None else ev["max_w"]) * 7
        if lo <= days <= hi:
            out.append({"occasion": ev["name"], "key": ev["key"], "keyword": ev["keyword"], "days_until": days})
    out.sort(key=lambda o: o["days_until"])
    return out


def _in_window(ev: dict, today: date) -> bool:
    d = _next_occurrence(ev, today)
    days = (d - today).days
    return ev["min_w"] * 7 <= days <= ev["max_w"] * 7


def seasonal_seed_keywords(today: date = None) -> list:
    """1-2 in-season seed keywords to fold into the trend pull."""
    return [o["keyword"] for o in upcoming_occasions(today)][:2]


def occasion_for(name: str, description: str = "") -> str:
    """1-4: the event KEY a concept is for (by keyword match), or None. Unlike
    occasion_mismatch this is date-independent — it just labels the product."""
    text = f"{name or ''} {description or ''}".lower()
    for ev in _EVENTS:
        if any(kw in text for kw in ev["match"]):
            return ev["key"]
    return None


def occasion_in_window(key: str, today: date = None) -> bool:
    """1-4: True if the event `key`'s buying window includes today."""
    today = today or date.today()
    ev = next((e for e in _EVENTS if e["key"] == key), None)
    return _in_window(ev, today) if ev else False


def occasion_mismatch(name: str, description: str = "", today: date = None) -> str:
    """1-3 hard gate: if the concept references an occasion whose window does NOT
    include today, return a rejection reason; else None. Deterministic — used in
    _validate_product exactly like the trademark screen."""
    today = today or date.today()
    text = f"{name or ''} {description or ''}".lower()
    for ev in _EVENTS:
        if any(kw in text for kw in ev["match"]):
            if not _in_window(ev, today):
                d = _next_occurrence(ev, today)
                days = (d - today).days
                if days < ev["min_w"] * 7:
                    when = f"is only {max(1, days // 7)} week(s) away — too soon for a brand-new listing to rank before it"
                else:
                    when = f"is {days // 7} weeks away — too far out to build now (its buying window opens ~{ev['max_w']} weeks before)"
                return (
                    f"concept is for {ev['name']}, which {when}. Do NOT build occasion products "
                    "outside their shopping window; propose an evergreen concept or one for a "
                    "currently in-window occasion."
                )
    return None


def seasonal_prompt_block(today: date = None) -> str:
    """A dated prompt block: what buyers are shopping for NOW (positive) and which
    occasions have passed / are too far out (negative — 1-3). '' if nothing near."""
    today = today or date.today()
    positive = upcoming_occasions(today)

    # Negative: recently passed (0-4 weeks after the event) or clearly too-far-out
    # (coming, but before the buying window opens, up to ~18 weeks).
    passed, far = [], []
    for ev in _EVENTS:
        d_this = ev["date"](today.year)
        # recently passed this year?
        if d_this < today and (today - d_this).days <= 28:
            passed.append(f"{ev['name']} (passed ~{max(1, (today - d_this).days // 7)} week(s) ago)")
            continue
        d_next = _next_occurrence(ev, today)
        days = (d_next - today).days
        if ev["max_w"] * 7 < days <= 126:
            far.append(f"{ev['name']} (~{days // 7} weeks away)")

    if not positive and not passed and not far:
        return ""

    parts = [f"\n\nSEASONAL TIMING — today is {today.isoformat()}."]
    if positive:
        names = ", ".join(f"{o['occasion']} (~{max(1, o['days_until'] // 7)} weeks away)" for o in positive)
        parts.append(
            f"Buyers are shopping NOW for: {names}. Strongly PREFER a concept tied to one of these "
            "when the trend/market data supports it."
        )
    neg = passed + far
    if neg:
        parts.append(
            "Do NOT propose products for these occasions (recently passed or too far out to rank in time): "
            + "; ".join(neg) + "."
        )
    return " ".join(parts)
