from app.core.validation.schema_validator import SchemaValidator

class QAAgent:

    def __init__(self):
        self.validator = SchemaValidator()

    def review(self, output: str):

        result = self.validator.validate_seo(output)

        return result

    def run(self, task: dict) -> dict:
        """
        Standardized entry point. Expects a task dict with an 'output'
        key containing the raw string to validate.
        """
        output = task.get("output", "")
        return self.review(output)