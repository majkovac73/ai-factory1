from app.core.validation.schema_validator import SchemaValidator


class SchemaAgent:
    """
    Agent wrapper around SchemaValidator. Kept for backward compatibility;
    new code should use SchemaValidator directly. This class is deprecated
    and may be removed in a future step.
    """

    def __init__(self):
        self.validator = SchemaValidator()

    def validate_seo(self, data: str) -> dict:
        """
        Delegates to SchemaValidator.validate_seo().
        
        Returns dict with keys:
          - valid: bool
          - data: dict (only if valid=True)
          - error: str (only if valid=False)
          - raw: str (original input, for debugging)
        """
        return self.validator.validate_seo(data)