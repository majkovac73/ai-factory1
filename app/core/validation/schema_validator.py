from app.core.schemas.seo_schema import SEOSchema
from app.core.utils.json_sanitizer import JSONSanitizer

class SchemaValidator:
    """
    Single entry point for all SEO schema validation. Handles JSON extraction,
    schema validation, and quality checks. All validation paths (QAAgent,
    TaskProcessor) should use this class to ensure consistency.
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

            # Extract JSON from potentially noisy output
            parsed = self.sanitizer.extract(data)

            # Validate against schema (includes length/count constraints)
            validated = SEOSchema(**parsed)

            return {
                "valid": True,
                "data": validated.model_dump()
            }

        except ValueError as e:
            error_msg = str(e)
            # Distinguish JSON extraction errors from schema validation errors
            if "Invalid JSON" in error_msg or "parsing failed" in error_msg:
                full_error = f"JSON extraction failed: {error_msg}"
            else:
                full_error = f"Schema validation failed: {error_msg}"
            
            return {
                "valid": False,
                "error": full_error,
                "raw": data
            }
        except Exception as e:
            return {
                "valid": False,
                "error": f"Unexpected validation error: {str(e)}",
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