"""Central settings loader for Garden."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = Field(default="Garden", alias="GARDEN_PROJECT_NAME")
    project_version: str = Field(default="0.1.0", alias="GARDEN_PROJECT_VERSION")
    environment: str = Field(default="development", alias="GARDEN_ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="GARDEN_LOG_LEVEL")
    api_host: str = Field(default="127.0.0.1", alias="GARDEN_API_HOST")
    api_port: int = Field(default=8000, alias="GARDEN_API_PORT")
    database_url: str = Field(
        default="sqlite+pysqlite:///./data/garden.db",
        alias="GARDEN_DATABASE_URL",
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
        default=False,
        alias="GARDEN_ALLOW_NON_LOCAL_TARGETS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
