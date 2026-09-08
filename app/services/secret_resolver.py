"""Secret reference resolution for demo and future integrations."""

from __future__ import annotations

import os

from app.core.errors import InputValidationError
from app.services.ephemeral_secret_store import EphemeralSecretStore


class SecretResolver:
    """Resolve secret references without exposing raw secrets in UI or CLI output."""

    def __init__(self, *, ephemeral_store: EphemeralSecretStore | None = None) -> None:
        self.ephemeral_store = ephemeral_store

    def resolve(self, secret_ref: str) -> str:
        if secret_ref.startswith("env://"):
            env_name = secret_ref.removeprefix("env://").strip()
            if not env_name:
                raise InputValidationError(
                    "env:// secret references must include an environment name."
                )
            value = os.getenv(env_name)
            if value is None:
                raise InputValidationError(f"Environment variable '{env_name}' is not set.")
            return value
        if secret_ref.startswith("literal://"):
            return secret_ref.removeprefix("literal://")
        if secret_ref.startswith("ephemeral-file://"):
            if self.ephemeral_store is None:
                raise InputValidationError("Temporary secret storage is not configured.")
            return self.ephemeral_store.read(secret_ref)
        raise InputValidationError(
            "Unsupported secret_ref scheme. Use env://VAR_NAME or literal://value for local demos."
        )
