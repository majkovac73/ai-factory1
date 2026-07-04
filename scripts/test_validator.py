from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.validation.schema_validator import SchemaValidator

v = SchemaValidator()

# Clean JSON - should pass
result = v.validate_seo('{"title": "Handmade Mug", "description": "Nice mug", "keywords": ["mug"], "sections": ["Intro"]}')
print("CLEAN JSON:", result["valid"])

# JSON with trailing commentary - should still pass now
result = v.validate_seo('{"title": "Handmade Mug", "description": "Nice mug", "keywords": ["mug"], "sections": ["Intro"]}\nHope that helps!')
print("TRAILING TEXT:", result["valid"])

# Empty string - should fail cleanly
result = v.validate_seo("")
print("EMPTY:", result["valid"], "-", result.get("error"))

# Garbage text - should fail cleanly
result = v.validate_seo("this is not json at all")
print("GARBAGE:", result["valid"], "-", result.get("error"))

# is_complete_json sanity check
print("STATIC METHOD CHECK:", SchemaValidator.is_complete_json('{"a": 1}'))