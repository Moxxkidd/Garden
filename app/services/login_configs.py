"""Login config parsing and loading."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.core.errors import InputValidationError
from app.schemas.auth import HttpLoginConfig, LoginConfig, PlaywrightLoginConfig


class LoginConfigService:
    """Load and validate login config files."""

    def load(self, config_path: str) -> LoginConfig:
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
