"""Health service for API and CLI use."""

from app.core.settings import get_settings
from app.db.bootstrap import check_database, init_database
from app.schemas.health import HealthResponse


def build_health_response() -> HealthResponse:
    settings = get_settings()
    init_database(settings.database_url)
    return HealthResponse(
        status="ok",
        project=settings.project_name,
        version=settings.project_version,
        database=check_database(),
        environment=settings.environment,
        allow_non_local_targets=settings.allow_non_local_targets,
    )
