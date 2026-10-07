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
    from app.services.identity_collection import IdentityCollectionService
    from app.services.identity_preview import IdentityPreviewService
    from app.services.manual_login import ManualLoginService

    app.state.identity_collection = IdentityCollectionService()
    app.state.identity_preview = IdentityPreviewService(app.state.identity_collection)
    app.state.manual_login = ManualLoginService(sessions=app.state.identity_collection.sessions)
    purged = app.state.scan_service.purge_expired_temporary_secrets(max_age_seconds=900)
    if purged:
        logger.warning("Purged expired temporary coverage secrets", extra={"count": len(purged)})
    interrupted = app.state.scan_service.interrupt_active_scans()
    if interrupted:
        logger.warning("Interrupted stale active scans", extra={"count": interrupted})
    try:
        yield
    finally:
        app.state.manual_login.shutdown()
        app.state.identity_collection.dispatcher.shutdown()
        app.state.scan_service.shutdown()
        logger.info("Shutting down Garden application")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.project_name,
        version=settings.project_version,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def identity_privacy_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/identities", "/api/identity", "/api/login-attempts")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    register_exception_handlers(app)
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
        name="static",
    )
    app.include_router(api_router)
    return app


app = create_app()
