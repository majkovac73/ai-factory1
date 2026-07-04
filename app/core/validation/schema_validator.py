from app.core.schemas.seo_schema import SEOSchema
from app.core.utils.json_sanitizer import JSONSanitizer

class SchemaValidator:
    """
    Single entry point for all SEO schema validation. Handles JSON extraction,
    schema validation, and quality checks. All validation paths (SchemaAgent,
    QAAgent, TaskProcessor) should use this class to ensure consistency.
    """

    def __init__(self):
        self.sanitizer = JSONSanitizer()

    def validate_seo(self, data: str):
        """
        Validates raw LLM output against SEO schema:
        1. Extracts JSON from markdown/noise
        2. Validates against SEOSchema (includes length/count constraints)
        3. Returns parsed dict if valid, error details if invalid

        Returns dict with keys:
          - valid: bool
          - data: dict (only if valid=True)
          - error: str (only if valid=False)
          - raw: str (original input, for debugging)
        """
        try:
            if not data or not data.strip():
                raise ValueError("Empty output cannot be validated")

            parsed = self.sanitizer.extract(data)
            validated = SEOSchema(**parsed)

            return {
                "valid": True,
                "data": validated.model_dump()
            }

        except ValueError as e:
            # Pydantic validation error (schema constraints violated)
            return {
                "valid": False,
                "error": f"Schema validation failed: {str(e)}",
                "raw": data
            }
        except Exception as e:
            # JSON extraction or other error
            return {
                "valid": False,
                "error": f"Output parsing failed: {str(e)}",
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