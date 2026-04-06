"""Secret reference resolution for demo and future integrations."""

from __future__ import annotations

import os

from app.core.errors import InputValidationError


class SecretResolver:
    """Resolve secret references without exposing raw secrets in UI or CLI output."""

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
        raise InputValidationError(
            "Unsupported secret_ref scheme. Use env://VAR_NAME or literal://value for local demos."
        )
