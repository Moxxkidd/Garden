"""Health routes."""

from fastapi import APIRouter

from app.schemas.health import HealthResponse
from app.services.health import build_health_response

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse, summary="Application health check")
def healthcheck() -> HealthResponse:
    return build_health_response()
