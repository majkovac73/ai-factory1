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


# 1-6: Hanukkah's first night falls on the Hebrew calendar, which needs a full
# Hebrew-date table to compute. A hardcoded {year: first-night} map for the years
# that matter is honest and correct (Gregorian dates of the first full day).
_HANUKKAH = {
    2025: date(2025, 12, 15),
    2026: date(2026, 12, 5),
    2027: date(2027, 12, 25),
    2028: date(2028, 12, 13),
    2029: date(2029, 12, 2),
    2030: date(2030, 12, 21),
}


def hanukkah(year: int) -> date:
    """First night of Hanukkah for `year` (from a hardcoded 2025-2030 table);
    falls back to ~Dec 10 for years outside the table so the gate still works."""
    return _HANUKKAH.get(year, date(year, 12, 10))


# 1-6: Diwali also follows a lunar calendar — hardcoded main-day dates.
_DIWALI = {
    2025: date(2025, 10, 21),
    2026: date(2026, 11, 8),
    2027: date(2027, 10, 29),
    2028: date(2028, 10, 17),
    2029: date(2029, 11, 5),
    2030: date(2030, 10, 26),
}


# ── event table (1-1 dates + 1-2 per-event windows + 1-3 match keywords) ────
# Each event: key, name, date_fn(year)->date, seed keyword, lead window (weeks),
# and the keywords used to detect that a concept is FOR this occasion.
# 7-3: each event carries `seeds` — its 2-3 proven, high-volume Etsy search
# phrases. When the occasion is in its buying window these fold into the trend
# pull (seasonal_seed_keywords) so research reflects what buyers are actually
# typing this season, not just the single generic "<occasion> printable".
_EVENTS = [
    # 1-6: New Year's EVE party goods (distinct from the New Year planning season).
    # MUST precede new_year so the more-specific "new year's eve" match wins over
    # new_year's broad "new year" substring.
    {"key": "nye_party", "name": "New Year's Eve party", "date": lambda y: date(y, 12, 31),
     "keyword": "new years eve party printable", "min_w": 2, "max_w": 7,
     "seeds": ["new years eve party printable", "nye party printable"],
     "match": ["new year's eve", "new years eve", "nye party", "nye printable"]},
    {"key": "new_year", "name": "New Year / planning season", "date": lambda y: date(y, 1, 1),
     "keyword": "new year planner", "min_w": 2, "max_w": 8,
     "seeds": ["new year planner printable", "goal planner printable", "vision board printable"],
     "match": ["new year", "resolution", "goal planner", "2026 planner", "2027 planner", "2028 planner"]},
    {"key": "valentines", "name": "Valentine's Day", "date": lambda y: date(y, 2, 14),
     "keyword": "valentines printable", "min_w": 4, "max_w": 9,
     "seeds": ["valentines day printable", "valentine card printable", "galentines printable"],
     "match": ["valentine", "valentines", "galentine", "cupid"]},
    {"key": "st_patricks", "name": "St. Patrick's Day", "date": lambda y: date(y, 3, 17),
     "keyword": "st patricks day printable", "min_w": 4, "max_w": 9,
     "seeds": ["st patricks day printable", "shamrock printable"],
     "match": ["st patrick", "st. patrick", "saint patrick", "shamrock", "leprechaun"]},
    {"key": "cinco_de_mayo", "name": "Cinco de Mayo", "date": lambda y: date(y, 5, 5),
     "keyword": "cinco de mayo printable", "min_w": 4, "max_w": 9,
     "seeds": ["cinco de mayo printable", "fiesta printable"],
     "match": ["cinco de mayo", "fiesta", "papel picado"]},
    {"key": "july_4th", "name": "4th of July / Independence Day", "date": lambda y: date(y, 7, 4),
     "keyword": "4th of july printable", "min_w": 4, "max_w": 9,
     "seeds": ["4th of july printable", "patriotic printable"],
     "match": ["4th of july", "fourth of july", "independence day", "stars and stripes", "patriotic"]},
    {"key": "easter", "name": "Easter / spring", "date": lambda y: easter(y),
     "keyword": "easter printable", "min_w": 4, "max_w": 9,
     "seeds": ["easter printable", "easter coloring page", "easter basket tags"],
     "match": ["easter", "easter bunny", "easter egg"]},
    {"key": "mothers_day", "name": "Mother's Day", "date": lambda y: nth_weekday(y, 5, 6, 2),
     "keyword": "mothers day printable", "min_w": 4, "max_w": 9,
     "seeds": ["mothers day printable", "mothers day card printable", "gift for mom printable"],
     "match": ["mother's day", "mothers day", "mom gift", "gift for mom"]},
    {"key": "graduation", "name": "Graduation season", "date": lambda y: date(y, 6, 1),
     "keyword": "graduation printable", "min_w": 4, "max_w": 9,
     "seeds": ["graduation printable", "graduation card printable", "class of 2026 printable"],
     "match": ["graduation", "graduate", "class of", "grad gift"]},
    {"key": "fathers_day", "name": "Father's Day", "date": lambda y: nth_weekday(y, 6, 6, 3),
     "keyword": "fathers day printable", "min_w": 4, "max_w": 9,
     "seeds": ["fathers day printable", "fathers day card printable", "gift for dad printable"],
     "match": ["father's day", "fathers day", "dad gift", "gift for dad"]},
    {"key": "back_to_school", "name": "Back to school", "date": lambda y: date(y, 9, 1),
     "keyword": "back to school printable", "min_w": 4, "max_w": 10,
     "seeds": ["back to school printable", "teacher appreciation printable", "classroom printable"],
     "match": ["back to school", "classroom", "teacher appreciation", "school planner"]},
    {"key": "halloween", "name": "Halloween", "date": lambda y: date(y, 10, 31),
     "keyword": "halloween printable", "min_w": 4, "max_w": 9,
     "seeds": ["halloween printable", "halloween coloring page", "spooky printable"],
     "match": ["halloween", "spooky", "pumpkin", "trick or treat"]},
    {"key": "thanksgiving", "name": "Thanksgiving", "date": lambda y: nth_weekday(y, 11, 3, 4),
     "keyword": "thanksgiving printable", "min_w": 4, "max_w": 9,
     "seeds": ["thanksgiving printable", "gratitude printable", "friendsgiving printable"],
     "match": ["thanksgiving", "turkey day", "friendsgiving", "gratitude"]},
    {"key": "hanukkah", "name": "Hanukkah", "date": lambda y: hanukkah(y),
     "keyword": "hanukkah printable", "min_w": 4, "max_w": 9,
     "seeds": ["hanukkah printable", "menorah printable"],
     "match": ["hanukkah", "chanukah", "menorah", "dreidel"]},
    {"key": "christmas", "name": "Christmas / holidays", "date": lambda y: date(y, 12, 25),
     "keyword": "christmas printable", "min_w": 6, "max_w": 14,
     "seeds": ["christmas printable", "christmas gift tags printable", "christmas coloring page"],
     "match": ["christmas", "xmas", "santa", "holiday gift", "advent"]},
    # 1-6: weddings sell YEAR-ROUND — a match-only event with a full-year window so
    # occasion_for() can stamp/track them (and the lifecycle leaves them active)
    # but occasion_mismatch() never rejects a wedding concept as "out of season",
    # and they are never seeded for timed building. "match_only" excludes them
    # from the seed pool and the "shop now" prompt list.
    {"key": "weddings", "name": "Wedding season", "date": lambda y: date(y, 5, 1),
     "keyword": "wedding printable", "min_w": 0, "max_w": 52, "match_only": True,
     "match": ["wedding", "bridal shower", "bachelorette", "bride to be", "save the date"]},
    # 1-6: Diwali (movable — small hardcoded table, honest for the years we run).
    {"key": "diwali", "name": "Diwali", "date": lambda y: _DIWALI.get(y, date(y, 11, 1)),
     "keyword": "diwali printable", "min_w": 3, "max_w": 8,
     "seeds": ["diwali printable", "rangoli printable"],
     "match": ["diwali", "deepavali", "rangoli"]},
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
        # 1-6: match-only events (e.g. weddings) are year-round; they exist for
        # the gate to recognize/stamp them, not to advertise as a timed "shop now".
        if ev.get("match_only"):
            continue
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


def seasonal_seed_keywords(today: date = None, max_seeds: int = 6) -> list:
    """7-3: fold each IN-WINDOW occasion's 2-3 proven Etsy search phrases into
    the trend pull (soonest occasion first), so research reflects what buyers
    are actually shopping for this season — not just one generic term. Bounded
    by max_seeds because each phrase is a separate pytrends fetch (rate-limit /
    429 risk). Falls back to the single generic keyword for events with no
    curated seeds."""
    out = []
    for o in upcoming_occasions(today):
        ev = next((e for e in _EVENTS if e["key"] == o["key"]), None)
        seeds = (ev or {}).get("seeds") or [o["keyword"]]
        for s in seeds:
            if s not in out:
                out.append(s)
            if len(out) >= max_seeds:
                return out
    return out


def occasion_for(name: str, description: str = "") -> str:
    """1-4: the event KEY a concept is for (by keyword match), or None. Unlike
    occasion_mismatch this is date-independent — it just labels the product.

    2-7: match ALL events and prefer the LONGEST matched keyword (most specific)
    so "Christmas gratitude planner" maps to christmas (matched 'christmas', 9
    chars) not thanksgiving (matched 'gratitude', 9)/first-in-table — table
    ordering no longer decides, specificity does."""
    text = f"{name or ''} {description or ''}".lower()
    best_key, best_score = None, None
    for ev in _EVENTS:
        for kw in ev["match"]:
            pos = text.find(kw)
            if pos < 0:
                continue
            # prefer the LONGEST match; break ties by the EARLIEST position in the
            # text (a title usually leads with its true subject).
            score = (len(kw), -pos)
            if best_score is None or score > best_score:
                best_key, best_score = ev["key"], score
    return best_key


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
