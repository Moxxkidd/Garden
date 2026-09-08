"""Application exception types and FastAPI handlers."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class GardenError(Exception):
    """Base exception for Garden domain/runtime errors."""


class InputValidationError(GardenError):
    """Raised when input data fails Garden-specific validation."""


class TargetPolicyError(GardenError):
    """Raised when a target violates current safety policy."""


class ResourceNotFoundError(GardenError):
    """Raised when a requested record cannot be found."""


class ConflictError(GardenError):
    """Raised when an operation conflicts with current persisted state."""


class OverallScanTimeout(RuntimeError):
    """Control-flow signal raised when an assessment exhausts its overall deadline."""


class ScanInterrupted(RuntimeError):
    """Control-flow signal raised when persisted cancellation is observed."""


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ResourceNotFoundError)
    async def handle_not_found_error(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        logger.warning("Requested resource was not found", extra={"path": request.url.path})
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def handle_conflict_error(request: Request, exc: ConflictError) -> JSONResponse:
        logger.warning("Request conflicted with persisted state", extra={"path": request.url.path})
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InputValidationError)
    async def handle_input_validation_error(
        request: Request, exc: InputValidationError
    ) -> JSONResponse:
        logger.warning("Input validation failed", extra={"path": request.url.path})
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(TargetPolicyError)
    async def handle_target_policy_error(request: Request, exc: TargetPolicyError) -> JSONResponse:
        logger.warning("Blocked target by safety policy", extra={"path": request.url.path})
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning("Request validation failed", extra={"path": request.url.path})
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled application exception", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error."},
        )
