from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.schemas.seo_schema import SEOSchema
from pydantic import ValidationError

# Should pass
try:
    valid = SEOSchema(
        title="Handmade Ceramic Mug",
        description="A lovely mug.",
        keywords=["mug", "ceramic"],
        sections=["Intro", "Details"],
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