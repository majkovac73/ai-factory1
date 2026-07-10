"""
Trademark / IP screening (STEP 103 C-1) — the single biggest existential risk
to the shop. Google's rising queries are full of brand/character/celebrity terms
BECAUSE they trend; one AI-generated "Bluey coloring page" or "Taylor Swift
wallpaper" listing is a takedown, and repeats mean permanent Etsy suspension
(which kills every legitimate listing too).

This is a static, env-extendable blocklist checked against concept names,
descriptions, tags, and the trend queries fed into prompts. Fail CLOSED: a hit
rejects the concept / drops the poisoned query or tag. It is backed up by an LLM
screen baked into the viability-critic rubric (zero extra calls).

The list is deliberately non-exhaustive — it catches the obvious, high-frequency
offenders. The LLM screen is the general safety net for everything else.
"""
import re

from config import settings

# Lowercased brand/franchise/character/celebrity/sports terms. Matched as whole
# words/phrases (word-boundary-ish) so "art" inside "artist" never trips.
_BLOCKLIST = {
    # Entertainment franchises / characters
    "disney", "pixar", "marvel", "dc comics", "star wars", "harry potter",
    "pokemon", "pokémon", "bluey", "peppa pig", "paw patrol", "spongebob",
    "hello kitty", "sanrio", "minecraft", "fortnite", "super mario", "nintendo",
    "sonic the hedgehog", "frozen elsa", "mickey mouse", "winnie the pooh",
    "barbie", "spiderman", "spider-man", "batman", "superman", "lilo & stitch",
    "studio ghibli", "totoro", "sesame street", "cocomelon", "the grinch",
    "dr seuss", "disney princess", "star trek", "lord of the rings",
    # Music / celebrities
    "taylor swift", "swiftie", "beyonce", "beyoncé", "drake", "billie eilish",
    "bad bunny", "olivia rodrigo", "kardashian", "kanye west", "travis scott",
    "harry styles", "ariana grande",
    # Brands
    "nike", "adidas", "gucci", "chanel", "louis vuitton", "supreme", "starbucks",
    "stanley cup", "coca cola", "coca-cola", "lululemon", "in-n-out", "prada",
    "north face", "jack daniels",
    # Sports leagues / events
    "nfl", "nba", "mlb", "nhl", "fifa", "super bowl", "olympics", "world cup",
}


def _extra_terms() -> set:
    raw = getattr(settings, "TRADEMARK_BLOCKLIST_EXTRA", None) or []
    return {t.strip().lower() for t in raw if isinstance(t, str) and t.strip()}


def blocklist() -> set:
    """Effective blocklist = built-in + TRADEMARK_BLOCKLIST_EXTRA env terms."""
    return _BLOCKLIST | _extra_terms()


def find_trademark(text: str):
    """Return the first blocklisted term found in `text` (whole-word/phrase
    match), or None. Case-insensitive."""
    if not text:
        return None
    low = f" {text.lower()} "
    for term in blocklist():
        # Boundaries that treat spaces/punctuation as separators but not letters,
        # so multi-word phrases and single words both match without partials.
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", low):
            return term
    return None


def screen(*texts):
    """Return the first trademark hit across all given texts, else None."""
    for t in texts:
        hit = find_trademark(t or "")
        if hit:
            return hit
    return None


def filter_tags(tags):
    """Drop any tag containing a blocklisted term. Returns (clean, dropped)."""
    clean, dropped = [], []
    for tag in tags or []:
        if find_trademark(str(tag)):
            dropped.append(tag)
        else:
            clean.append(tag)
    return clean, dropped


def filter_queries(queries):
    """Drop poisoned trend queries before they reach the research/concept prompt.
    Returns the clean list."""
    return [q for q in (queries or []) if not find_trademark(str(q))]
