"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.db.bootstrap import ensure_database_at_head, init_database
from app.services.scan_application import ScanApplicationService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    logger.info("Starting Garden application", extra={"environment": settings.environment})
    ensure_database_at_head(
        settings.database_url,
        environment=settings.environment,
        auto_migrate=settings.database_auto_migrate,
    )
    init_database(settings.database_url)
    app.state.scan_service = ScanApplicationService(settings=settings)
    interrupted = app.state.scan_service.interrupt_active_scans()
    if interrupted:
        logger.warning("Interrupted stale active scans", extra={"count": interrupted})
    try:
        yield
    finally:
        app.state.scan_service.shutdown()
        logger.info("Shutting down Garden application")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.project_name,
        version=settings.project_version,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
        name="static",
    )
    app.include_router(api_router)
    return app


app = create_app()
