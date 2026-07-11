"""
Seasonality (STEP 103 A-7). Etsy buyers shop occasions 4-10 weeks ahead
(Christmas printables peak in October, Valentine's cards in early January,
back-to-school planners in July). Trailing Google Trends catches a wave as it
crests — after Etsy's ranking has consolidated winners with reviews. This
surfaces the occasions buyers are shopping for RIGHT NOW so the concept
generator can be structurally early instead of late.
"""
from datetime import date

# (name, month, day, seed_keyword) — approximate dates for movable feasts.
_EVENTS = [
    ("New Year / planning season", 1, 1, "new year planner"),
    ("Valentine's Day", 2, 14, "valentines printable"),
    ("St. Patrick's Day", 3, 17, "st patricks day printable"),
    ("Easter / spring", 4, 9, "easter printable"),
    ("Mother's Day", 5, 11, "mothers day printable"),
    ("Graduation season", 6, 1, "graduation printable"),
    ("Father's Day", 6, 15, "fathers day printable"),
    ("Back to school", 9, 1, "back to school printable"),
    ("Halloween", 10, 31, "halloween printable"),
    ("Thanksgiving", 11, 27, "thanksgiving printable"),
    ("Christmas / holidays", 12, 25, "christmas printable"),
]


def upcoming_occasions(today: date = None, min_weeks: int = 3, max_weeks: int = 10) -> list:
    """Occasions whose 4-10-week shopping window includes today, soonest first."""
    today = today or date.today()
    out = []
    for name, m, d, kw in _EVENTS:
        try:
            ev = date(today.year, m, d)
        except ValueError:
            continue
        if ev < today:
            try:
                ev = date(today.year + 1, m, d)
            except ValueError:
                continue
        days = (ev - today).days
        if min_weeks * 7 <= days <= max_weeks * 7:
            out.append({"occasion": name, "keyword": kw, "days_until": days})
    out.sort(key=lambda o: o["days_until"])
    return out


def seasonal_seed_keywords(today: date = None) -> list:
    """1-2 seasonal seed keywords to fold into the trend pull when in season."""
    return [o["keyword"] for o in upcoming_occasions(today)][:2]


def seasonal_prompt_block(today: date = None) -> str:
    """A prompt block naming what buyers are shopping for now; '' if nothing near."""
    today = today or date.today()
    occ = upcoming_occasions(today)
    if not occ:
        return ""
    names = ", ".join(f"{o['occasion']} (~{max(1, o['days_until'] // 7)} weeks away)" for o in occ)
    return (
        f"\n\nSEASONAL TIMING — it is {today.isoformat()}. Etsy buyers shop occasions 4-10 "
        f"weeks AHEAD, and they are shopping NOW for: {names}. Strongly prefer a concept tied to "
        "one of these occasions when the trend/market data supports it (well-timed occasion "
        "products sell far better than generic evergreen ones)."
    )
