"""
Step 103 / A-4 test — SEO overhaul: 13 tags always, real materials, structured
description blocks.

  [1] _derive_tags pads to exactly 13 valid tags from few keywords + title,
      deduped, each <=20 chars, >=3 chars, phrases preferred.
  [2] _derive_tags respects existing good keywords and never exceeds 13.
  [3] materials_for returns real per-format materials (never empty).
  [4] description_blocks produces WHAT YOU GET / HOW IT WORKS / TERMS, with
      digital vs POD "how it works" wording and PDF page count.

Usage: python scripts/test_step103_seo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.etsy.listing_generator import ListingGeneratorAgent
from app.core.product_formats import materials_for, description_blocks

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


gen = ListingGeneratorAgent()

# [1] pad to 13 from few keywords
tags = gen._derive_tags(["boho wall art", "nursery print"], product_name="Sage Green Botanical Line Art Print")
check("1 exactly 13 tags", len(tags) == 13)
check("1 all <=20 chars", all(len(t) <= 20 for t in tags))
check("1 all >=3 chars", all(len(t) >= 3 for t in tags))
check("1 no duplicates", len(set(t.lower() for t in tags)) == 13)
check("1 keeps the original keywords", "boho wall art" in tags and "nursery print" in tags)

# [2] never exceeds 13 even with many keywords
many = [f"keyword phrase {i}" for i in range(30)]
tags2 = gen._derive_tags(many, product_name="Some Product")
check("2 capped at 13", len(tags2) == 13)

# [3] materials
check("3 single_print materials non-empty", len(materials_for("single_print")) >= 1)
check("3 pod materials mention cotton/tshirt", any("cotton" in m or "shirt" in m for m in materials_for("pod_apparel_design")))
check("3 unknown format falls back", materials_for("nope") == ["digital download"])

# [4] description blocks
d_digital = description_blocks("single_print")
check("4 has WHAT YOU GET", "WHAT YOU GET" in d_digital)
check("4 has HOW IT WORKS", "HOW IT WORKS" in d_digital)
check("4 has TERMS", "TERMS" in d_digital)
check("4 digital says instant download", "Instant digital download" in d_digital)

d_pod = description_blocks("pod_apparel_design")
check("4 POD says made to order", "Made to order" in d_pod)
check("4 POD not instant download", "Instant digital download" not in d_pod)

d_pdf = description_blocks("pdf_planner_or_guide", page_count=12)
check("4 PDF mentions page count", "12 pages" in d_pdf)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-103 SEO tests passed.")
