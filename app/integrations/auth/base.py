"""Base auth adapter interfaces."""

from __future__ import annotations

from typing import Protocol

from app.models.credential_profile import CredentialProfile
from app.models.target import Target
from app.schemas.auth import LoginConfig, LoginExecutionResult, SessionValidationResult


class AuthAdapter(Protocol):
    adapter_name: str

    def login(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        secret_value: str,
        config: LoginConfig,
    ) -> LoginExecutionResult: ...

    def validate(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        config: LoginConfig,
        stored_payload: dict[str, object],
    ) -> SessionValidationResult: ...

    def refresh(
        self,
        target: Target,
        credential_profile: CredentialProfile,
        secret_value: str,
        config: LoginConfig,
        stored_payload: dict[str, object],
    ) -> LoginExecutionResult: ...
