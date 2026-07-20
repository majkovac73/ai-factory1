"""
Audit 2026-07-20 #7 — SEO tags: no mid-word truncation, always exactly 13 valid.

Verifies ListingGeneratorAgent:
  [1] _to_valid_tag keeps whole words <= 20 chars (never "classroom organizati").
  [2] validate_tags rejects >20-char / unstripped / short tags.
  [3] _derive_tags always returns exactly 13 tags, all valid (<=20, stripped),
      even from a sparse keyword list (backfills instead of under-filling).

Usage: python scripts/test_audit_step7_seo_tags.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.etsy.listing_generator import ListingGeneratorAgent as LG

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# [1] whole-word truncation ---------------------------------------------------
check("1 'classroom organization' -> whole word, no mid-word cut",
      LG._to_valid_tag("classroom organization") == "classroom")  # 22 chars -> drop 2nd word
check("1 'printable teacher tags' -> keeps first two words <=20",
      LG._to_valid_tag("printable teacher tags") in ("printable teacher", "printable teacher tags"[:20]) and
      len(LG._to_valid_tag("printable teacher tags")) <= 20)
check("1 short phrase preserved", LG._to_valid_tag("wall art") == "wall art")
check("1 single overlong word -> None (unsalvageable)",
      LG._to_valid_tag("supercalifragilisticexpialidocious") is None)
check("1 all tags produced are <=20 and whole-word",
      LG._to_valid_tag("inspiring quotes tags for classroom") is not None and
      len(LG._to_valid_tag("inspiring quotes tags for classroom")) <= 20)

# [2] validate_tags -----------------------------------------------------------
v = LG.validate_tags(["ok tag", "waytoolongtagphrase over 20 chars", " leading", "x", "ok tag"])
check("2 drops >20-char tag", "waytoolongtagphrase over 20 chars" not in v)
check("2 keeps valid", "ok tag" in v)
check("2 de-dupes", v.count("ok tag") == 1)
check("2 all validated <=20 & stripped", all(len(t) <= 20 and t == t.strip() for t in v))

# [3] always exactly 13 valid -------------------------------------------------
gen = LG.__new__(LG)
tags = gen._derive_tags(keywords=["classroom organization", "teacher"], product_name="Classroom Organization Planner")
check("3 exactly 13 tags", len(tags) == 13)
check("3 all <=20 chars", all(len(t) <= 20 for t in tags))
check("3 all stripped, >=3 chars", all(t == t.strip() and len(t) >= 3 for t in tags))
check("3 no duplicates", len(set(t.lower() for t in tags)) == 13)
check("3 no mid-word truncation artifact", "classroom organizati" not in tags)

# even with an empty keyword list, filler guarantees 13
tags2 = gen._derive_tags(keywords=[], product_name="")
check("3 empty input still fills 13 valid tags",
      len(tags2) == 13 and all(len(t) <= 20 for t in tags2))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#7 SEO-tag tests passed.")
