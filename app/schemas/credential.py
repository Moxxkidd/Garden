"""Credential profile input schemas."""

from pydantic import BaseModel, Field, field_validator

from app.core.errors import InputValidationError
from app.models.enums import AuthType


class CredentialProfileCreate(BaseModel):
    target_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    auth_type: AuthType
    username: str = Field(min_length=1, max_length=255)
    secret_ref: str = Field(min_length=1, max_length=255)
    login_config_path: str = Field(min_length=1, max_length=4000)

    @field_validator("name", "role", "username", "secret_ref", "login_config_path")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise InputValidationError("Credential fields cannot be blank.")
        return cleaned
