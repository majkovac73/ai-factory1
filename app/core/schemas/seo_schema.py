from pydantic import BaseModel
from pydantic import Field, field_validator
from typing import List, Union

class SEOSchema(BaseModel):
    # P1-9: Etsy allows 140-char titles and weights them heavily in search;
    # capping at 70 halved the long-tail keyword surface (discoverability = money).
    title: str = Field(..., min_length=20, max_length=140, description="Etsy listing title (20-140 chars, keyword-rich)")
    description: str = Field(..., min_length=120, description="Full product description (120+ chars)")
    keywords: List[str] = Field(..., min_length=3, description="SEO search terms (3+ required)")
    sections: List[Union[str, dict]] = Field(..., min_length=4, description="List of section strings or section objects (4+ required)")

    @field_validator("keywords")
    @classmethod
    def keywords_not_empty_strings(cls, value):
        cleaned = [k.strip() for k in value if isinstance(k, str) and k.strip()]
        if not cleaned:
            raise ValueError("keywords must contain at least one non-empty search term")
        return cleaned

    @field_validator("sections", mode="before")
    @classmethod
    def normalize_sections(cls, value):
        if not isinstance(value, list):
            raise ValueError("sections must be a list")

        normalized = []
        for item in value:
            if isinstance(item, str):
                if not item.strip():
                    raise ValueError("section string must not be empty")
                normalized.append(item)
            elif isinstance(item, dict):
                content = item.get("content") or item.get("text")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("section object must contain a non-empty string 'content'")
                normalized.append(content)
            else:
                raise ValueError("sections list items must be strings or objects")
        return normalized