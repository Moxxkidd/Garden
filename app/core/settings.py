"""Central settings loader for Garden."""

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, DotEnvSettingsSource, SettingsConfigDict

from app.cli.paths import GardenPaths


def _is_formal_cli_runtime() -> bool:
    return os.environ.get("GARDEN_CLI_RUNTIME") == "1"


def _default_database_url() -> str:
    if _is_formal_cli_runtime():
        return GardenPaths.from_environment().database_url
    return "sqlite+pysqlite:///./data/garden.db"


class Settings(BaseSettings):
    project_name: str = Field(default="Garden", alias="GARDEN_PROJECT_NAME")
    project_version: str = Field(default="0.2.0", alias="GARDEN_PROJECT_VERSION")
    environment: str = Field(default="development", alias="GARDEN_ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="GARDEN_LOG_LEVEL")
    api_host: str = Field(default="127.0.0.1", alias="GARDEN_API_HOST")
    api_port: int = Field(default=8000, alias="GARDEN_API_PORT")
    database_url: str = Field(default_factory=_default_database_url, alias="GARDEN_DATABASE_URL")
    database_auto_migrate: bool = Field(
        default=False,
        alias="GARDEN_DATABASE_AUTO_MIGRATE",
    )
    demo_admin_password: str = Field(
        default="demo-admin-password",
        alias="GARDEN_DEMO_ADMIN_PASSWORD",
    )
    demo_user_password: str = Field(
        default="demo-user-password",
        alias="GARDEN_DEMO_USER_PASSWORD",
    )
    allow_non_local_targets: bool = Field(
        default=True,
        alias="GARDEN_ALLOW_NON_LOCAL_TARGETS",
    )
    allow_private_targets: bool = Field(
        default=False,
        alias="GARDEN_ALLOW_PRIVATE_TARGETS",
    )
    scan_proxy_url: str | None = Field(default=None, alias="GARDEN_SCAN_PROXY_URL")
    scan_no_proxy: str = Field(
        default="localhost,127.0.0.1,::1",
        alias="GARDEN_SCAN_NO_PROXY",
    )
    scan_request_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
        alias="GARDEN_SCAN_REQUEST_TIMEOUT_SECONDS",
    )
    scan_overall_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        le=1800,
        alias="GARDEN_SCAN_OVERALL_TIMEOUT_SECONDS",
    )
    scan_retry_attempts: int = Field(
        default=1,
        ge=0,
        le=2,
        alias="GARDEN_SCAN_RETRY_ATTEMPTS",
    )
    scan_max_concurrent_tasks: int = Field(
        default=2,
        ge=1,
        le=8,
        alias="GARDEN_SCAN_MAX_CONCURRENT_TASKS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        if _is_formal_cli_runtime():
            formal_dotenv = DotEnvSettingsSource(
                settings_cls,
                env_file=GardenPaths.from_environment().config_file,
                env_file_encoding="utf-8",
                case_sensitive=False,
            )
            return init_settings, env_settings, formal_dotenv, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
