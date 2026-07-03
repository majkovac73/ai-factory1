from app.core.validation.schema_validator import SchemaValidator

class QAAgent:

    def __init__(self):
        self.validator = SchemaValidator()

    def review(self, output: str):

        result = self.validator.validate_seo(output)

        return result