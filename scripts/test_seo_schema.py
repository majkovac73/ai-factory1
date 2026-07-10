from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.schemas.seo_schema import SEOSchema
from pydantic import ValidationError

# Should pass
try:
    valid = SEOSchema(
        title="Handmade Ceramic Coffee Mug",
        # description must be >=120 chars, keywords >=3, sections >=4 per the
        # current SEOSchema minimums (fixture was stale — pre-dated tightening).
        description=(
            "A lovely handmade ceramic coffee mug, glazed by hand and finished "
            "for everyday use. Microwave and dishwasher safe, it makes a warm, "
            "thoughtful gift for coffee and tea lovers alike."
        ),
        keywords=["mug", "ceramic", "handmade", "coffee"],
        sections=["Intro", "Details", "Care", "Shipping"],
    )
    print("VALID CASE PASSED:", valid.title)
except ValidationError as e:
    print("VALID CASE UNEXPECTEDLY FAILED:", e)

# Should fail - empty keywords
try:
    SEOSchema(title="Mug", description="desc", keywords=[], sections=["a"])
    print("EMPTY KEYWORDS: incorrectly passed")
except ValidationError:
    print("EMPTY KEYWORDS: correctly rejected")

# Should fail - title too long
try:
    SEOSchema(title="x" * 200, description="desc", keywords=["a"], sections=["a"])
    print("LONG TITLE: incorrectly passed")
except ValidationError:
    print("LONG TITLE: correctly rejected")

# Should fail - blank section
try:
    SEOSchema(title="Mug", description="desc", keywords=["a"], sections=["   "])
    print("BLANK SECTION: incorrectly passed")
except ValidationError:
    print("BLANK SECTION: correctly rejected")