"""Target input and import schemas."""

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.core.errors import InputValidationError
from app.models.enums import TargetStatus, TargetType


def normalize_base_url(value: str) -> str:
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise InputValidationError("base_url must be a valid http or https URL.")
    return cleaned.rstrip("/")


def normalize_tags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in values:
        cleaned = tag.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


class TargetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str
    type: TargetType
    owner: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list)
    status: TargetStatus = TargetStatus.ACTIVE

    @field_validator("name", "owner")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise InputValidationError("Text fields cannot be blank.")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_base_url(value)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return normalize_tags(value)
