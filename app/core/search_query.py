"""
Shared market-search query normalization (STEP 106 1-5).

The Etsy competition/price lookup must search the NICHE, not the whole product
name. Searching "Woodland Dreams Nursery Animal Print Set" returns a handful of
listings (competition looks tiny → 10/10) while the real niche "nursery animal
print" has 50k+ rivals. This strips filler/stopwords and keeps the first few
content tokens — the same normalization the SEO-refresh service already used,
extracted here so both paths share it.
"""

# Buyer-search filler that inflates specificity without narrowing the niche.
_STOP = {
    "the", "and", "for", "with", "your", "you", "our", "a", "an", "of", "to",
    "in", "on", "printable", "digital", "instant", "download", "set", "pack",
    "bundle", "art", "print", "design", "file", "files", "pdf", "png",
    "high", "resolution", "instantdownload",
}


def normalize_market_query(text: str, max_tokens: int = 4) -> str:
    """Lowercase, strip punctuation + stopwords/filler, keep the first
    `max_tokens` content tokens (>2 chars). Returns '' if nothing survives."""
    if not text:
        return ""
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in str(text).lower())
    words = [w for w in cleaned.split() if len(w) > 2 and w not in _STOP]
    return " ".join(words[:max_tokens])


def head_niche_query(text: str) -> str:
    """The 2-token 'head' of the normalized query — a broader, more conservative
    niche estimate (used to take the LARGER competition count)."""
    norm = normalize_market_query(text, max_tokens=2)
    return norm
