from pydantic import BaseModel
from pydantic import Field, field_validator
from typing import List, Union

class SEOSchema(BaseModel):
    title: str
    description: str
    keywords: List[str]
    sections: List[Union[str, dict]] = Field(..., description="List of section strings or section objects")

    @field_validator("sections", mode="before")
    @classmethod
    def normalize_sections(cls, value):
        if not isinstance(value, list):
            raise ValueError("sections must be a list")

        normalized = []
        for item in value:
            if isinstance(item, str):
                normalized.append(item)
            elif isinstance(item, dict):
                content = item.get("content") or item.get("text")
                if not isinstance(content, str):
                    raise ValueError("section object must contain a string 'content'")
                normalized.append(content)
            else:
                raise ValueError("sections list items must be strings or objects")
        return normalized