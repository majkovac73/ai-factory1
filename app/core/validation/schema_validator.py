from app.core.schemas.seo_schema import SEOSchema
from app.core.utils.json_sanitizer import JSONSanitizer

class SchemaValidator:

    def __init__(self):
        self.sanitizer = JSONSanitizer()

    def validate_seo(self, data: str):

        try:
            if not data or not data.strip():
                raise ValueError("Empty output cannot be validated")

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

    @staticmethod
    def is_complete_json(text: str) -> bool:
        """
        Lightweight structural check, independent of full parsing —
        useful as a fast pre-filter elsewhere if needed. Not used in
        validate_seo() itself since JSONSanitizer already handles
        trailing/leading noise more robustly than a simple suffix check.
        """
        return bool(text) and text.strip().endswith("}")