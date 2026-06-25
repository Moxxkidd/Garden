"""Login config parsing and loading."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import yaml

from app.core.errors import InputValidationError
from app.schemas.auth import HttpLoginConfig, LoginConfig, PlaywrightLoginConfig

INLINE_CONFIG_PREFIX = "inline://"


def encode_inline_login_config(raw_data: dict) -> str:
    """Encode a login config for storage without an external YAML file."""
    payload = json.dumps(raw_data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{INLINE_CONFIG_PREFIX}{encoded}"


class LoginConfigService:
    """Load and validate login config files."""

    def load(self, config_path: str) -> LoginConfig:
        if config_path.startswith(INLINE_CONFIG_PREFIX):
            return self._load_inline(config_path)
        path = Path(config_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            raise InputValidationError(f"Login config '{path}' does not exist.")
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            raise InputValidationError("Login config must be a YAML object.")
        adapter_name = raw_data.get("adapter")
        if adapter_name == "http":
            return HttpLoginConfig.model_validate(raw_data)
        if adapter_name == "playwright":
            return PlaywrightLoginConfig.model_validate(raw_data)
        raise InputValidationError("Login config adapter must be 'http' or 'playwright'.")

    def _load_inline(self, config_path: str) -> LoginConfig:
        encoded = config_path.removeprefix(INLINE_CONFIG_PREFIX)
        if not encoded:
            raise InputValidationError("Inline login config is empty.")
        padded = encoded + ("=" * (-len(encoded) % 4))
        try:
            raw_data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as error:
            raise InputValidationError("Inline login config is malformed.") from error
        if not isinstance(raw_data, dict):
            raise InputValidationError("Inline login config must decode to an object.")
        adapter_name = raw_data.get("adapter")
        if adapter_name == "http":
            return HttpLoginConfig.model_validate(raw_data)
        if adapter_name == "playwright":
            return PlaywrightLoginConfig.model_validate(raw_data)
        raise InputValidationError("Login config adapter must be 'http' or 'playwright'.")
