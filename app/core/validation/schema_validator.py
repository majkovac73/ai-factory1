from app.core.schemas.seo_schema import SEOSchema
from app.core.utils.json_sanitizer import JSONSanitizer

class SchemaValidator:

    def __init__(self):
        self.sanitizer = JSONSanitizer()

    def validate_seo(self, data: str):

        try:
            if not data or not data.strip().endswith("}"):
                raise ValueError("Incomplete JSON output (truncated)")

            parsed = self.sanitizer.extract(data)

            validated = SEOSchema(**parsed)

            return {
                "valid": True,
                "data": validated.model_dump()
            }

        except Exception as e:
            return {
                "valid": False,
                "error": str(e),
                "raw": data
            }
    def is_complete_json(text: str):
        return text.strip().endswith("}")