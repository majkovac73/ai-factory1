from app.core.schemas.seo_schema import SEOSchema
from app.core.utils.json_sanitizer import JSONSanitizer

class SchemaAgent:

    def __init__(self):
        self.sanitizer = JSONSanitizer()

    def validate_seo(self, data: str) -> dict:

        try:
            if not data or not data.strip():
                raise ValueError("Empty output cannot be validated")

            parsed = self.sanitizer.extract(data)
            validated = SEOSchema(**parsed)

            self._ensure_quality(validated)

            return {
                "valid": True,
                "data": validated.model_dump()
            }

        except Exception as e:
            # If we were able to parse JSON but failed quality checks, include parsed
            try:
                parsed  # type: ignore
            except NameError:
                parsed = None

            result = {
                "valid": False,
                "error": str(e),
                "raw": data
            }

            if parsed is not None:
                result["parsed"] = parsed

            return result

    def _ensure_quality(self, validated: SEOSchema):
        title_len = len(validated.title.strip())
        if title_len < 20:
            raise ValueError("Title is too short; make it more specific and compelling.")
        if title_len > 70:
            raise ValueError("Title is too long; keep it concise for Etsy listings.")

        description_len = len(validated.description.strip())
        if description_len < 120:
            raise ValueError("Description is too short; add more persuasive and concrete product detail.")

        if len(validated.keywords) < 3:
            raise ValueError("Keywords array must include at least 3 strong search terms.")

        if len(validated.sections) < 4:
            raise ValueError("Sections must include at least 4 structured copy elements.")
